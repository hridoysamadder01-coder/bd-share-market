package main

import (
	"bytes"
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// runSource runs one transport for `d` (or until stop returns true, polled every 20 ms) and closes the store.
func runSource(t *testing.T, cfg SourceConfig, d time.Duration, stop func() bool) string {
	t.Helper()
	root := t.TempDir()
	st, err := OpenStore(root, "cap", "test", 64)
	if err != nil {
		t.Fatal(err)
	}
	tr, err := NewTransport(cfg, st)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), d)
	done := make(chan struct{})
	go func() { tr.Run(ctx); close(done) }()
	if stop != nil {
		for ctx.Err() == nil && !stop() {
			time.Sleep(20 * time.Millisecond)
		}
	} else {
		<-ctx.Done()
	}
	cancel()
	<-done
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := VerifyStore(root); !ok || err != nil {
		t.Fatalf("store does not verify: %v", err)
	}
	return root
}

func TestHTTPPollWritesEnvelopeAndExactBody(t *testing.T) {
	var n atomic.Int64
	body := "{\"t\":\"২০২৬\",\"v\":[1,2,3],\"html\":\"<a>&\"}\r\n"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n.Add(1)
		if r.Method != "POST" || r.FormValue("inst") != "ABC" || r.URL.Query().Get("q") != "1" || r.Header.Get("X-Test") != "yes" {
			http.Error(w, "bad request shape", 400)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Custom", "v")
		_, _ = w.Write([]byte(body))
	}))
	defer srv.Close()
	key := "ABC"
	root := runSource(t, SourceConfig{Name: "poll", Type: "http_poll", URL: srv.URL + "/depth", Form: map[string]string{"inst": "ABC"},
		Params: map[string]string{"q": "1"}, Headers: map[string]string{"X-Test": "yes"}, IntervalMs: 50, Key: &key},
		2*time.Second, func() bool { return n.Load() >= 4 })
	recs := readAll(t, root, "poll")
	data := ofKind(recs, "DATA")
	if len(data) < 4 || len(ofKind(recs, "GAP")) != 0 {
		t.Fatalf("data=%d gaps=%d", len(data), len(ofKind(recs, "GAP")))
	}
	r := data[0]
	got, _ := decodeBody(r)
	if string(got) != body || r["body_encoding"] != "utf8" || r["key"] != "ABC" || r["src_seq"] != nil {
		t.Fatalf("record %v", r)
	}
	env := r["http"].(map[string]any)
	if env["method"] != "POST" || env["status"].(float64) != 200 || env["form"].(map[string]any)["inst"] != "ABC" ||
		env["params"].(map[string]any)["q"] != "1" || env["tls_verify"] != true {
		t.Fatalf("envelope %v", env)
	}
	for _, k := range []string{"t_send_utc", "t_first_byte_utc", "t_last_byte_utc", "final_url", "elapsed_ms", "request_headers", "response_headers"} {
		if _, ok := env[k]; !ok {
			t.Fatalf("envelope lacks %s", k)
		}
	}
	if env["response_headers"].(map[string]any)["X-Custom"] != "v" || env["request_headers"].(map[string]any)["X-Test"] != "yes" {
		t.Fatalf("headers %v", env)
	}
	if !isoRe.MatchString(env["t_first_byte_utc"].(string)) || env["t_first_byte_utc"].(string) > env["t_last_byte_utc"].(string) {
		t.Fatalf("byte clocks %v", env)
	}
}

func TestHTTPPollTokenFlowAndErrors(t *testing.T) {
	var tokenFetches, dataOK atomic.Int64
	mux := http.NewServeMux()
	mux.HandleFunc("/Home/MarketDepth", func(w http.ResponseWriter, r *http.Request) {
		tokenFetches.Add(1)
		http.SetCookie(w, &http.Cookie{Name: "af", Value: "cookie1", Path: "/"})
		_, _ = w.Write([]byte(`<html><form><input name="__RequestVerificationToken" type="hidden" value="tok-XYZ" /></form></html>`))
	})
	mux.HandleFunc("/api/data", func(w http.ResponseWriter, r *http.Request) {
		c, err := r.Cookie("af")
		if r.Header.Get("RequestVerificationToken") != "tok-XYZ" || err != nil || c.Value != "cookie1" {
			http.Error(w, "forbidden", 403)
			return
		}
		dataOK.Add(1)
		_, _ = w.Write([]byte(`{"ok":true}`))
	})
	mux.HandleFunc("/broken", func(w http.ResponseWriter, r *http.Request) { http.Error(w, "boom", 503) })
	srv := httptest.NewServer(mux)
	defer srv.Close()

	root := runSource(t, SourceConfig{Name: "lb", Type: "http_poll", URL: srv.URL + "/api/data", IntervalMs: 30,
		TokenURL: srv.URL + "/Home/MarketDepth", TokenRegex: `name="__RequestVerificationToken"[^>]*value="([^"]+)"`},
		2*time.Second, func() bool { return dataOK.Load() >= 3 })
	recs := readAll(t, root, "lb")
	if len(ofKind(recs, "GAP")) != 0 || len(ofKind(recs, "DATA")) < 3 {
		t.Fatalf("token flow: gaps=%v", ofKind(recs, "GAP"))
	}
	if tokenFetches.Load() != 1 {
		t.Fatalf("token fetched %d times (ttl not honoured)", tokenFetches.Load())
	}
	env := ofKind(recs, "DATA")[0]["http"].(map[string]any)
	if env["request_headers"].(map[string]any)["RequestVerificationToken"] != "tok-XYZ" {
		t.Fatalf("token header not recorded: %v", env["request_headers"])
	}
	if _, has := env["request_headers"].(map[string]any)["Cookie"]; has {
		t.Fatal("cookies must not be recorded")
	}

	// non-2xx → GAP{reason:"http"} with envelope and body; connection refused → GAP{reason:"exception"}
	root = runSource(t, SourceConfig{Name: "bad", Type: "http_poll", URL: srv.URL + "/broken", IntervalMs: 30},
		400*time.Millisecond, nil)
	gaps := ofKind(readAll(t, root, "bad"), "GAP")
	if len(gaps) == 0 || gaps[0]["reason"] != "http" || gaps[0]["http"].(map[string]any)["status"].(float64) != 503 ||
		!strings.HasPrefix(gaps[0]["body"].(string), "boom") {
		t.Fatalf("http gap %v", gaps)
	}
	l, _ := net.Listen("tcp", "127.0.0.1:0")
	dead := "http://" + l.Addr().String() + "/x"
	l.Close()
	root = runSource(t, SourceConfig{Name: "dead", Type: "http_poll", URL: dead, IntervalMs: 30, TimeoutMs: 500}, 300*time.Millisecond, nil)
	gaps = ofKind(readAll(t, root, "dead"), "GAP")
	if len(gaps) == 0 || gaps[0]["reason"] != "exception" || gaps[0]["http"].(map[string]any)["exception"] == nil {
		t.Fatalf("exception gap %v", gaps)
	}
}

func TestTCPFIXFramingWithDisconnectAndReconnect(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	var conns atomic.Int64
	msgs := [][]byte{fixMsg(7, "55=ABC\x01"), fixMsg(8, "55=DEF\x01268=1\x01"), fixMsg(9, "55=GHI\x01")}
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			k := conns.Add(1)
			go func(c net.Conn, k int64) {
				defer c.Close()
				buf := make([]byte, 64)
				n, _ := c.Read(buf) // send_on_connect logon
				if !bytes.HasPrefix(buf[:n], []byte("8=FIX")) {
					return
				}
				if k == 1 {
					// first connection: three messages, one split mid-frame across writes, then hang up
					stream := bytes.Join(msgs, nil)
					_, _ = c.Write(stream[:len(stream)/2])
					time.Sleep(30 * time.Millisecond)
					_, _ = c.Write(stream[len(stream)/2:])
					time.Sleep(50 * time.Millisecond)
					return
				}
				_, _ = c.Write(fixMsg(10, "55=RECONNECTED\x01"))
				time.Sleep(3 * time.Second)
			}(c, k)
		}
	}()
	root := runSource(t, SourceConfig{Name: "fix", Type: "tcp", Addr: l.Addr().String(), Framing: "soh",
		SendOnConnect: []string{string(fixMsg(1, "35=A\x01"))}}, 5*time.Second,
		func() bool { return conns.Load() >= 2 })
	recs := readAll(t, root, "fix")
	data := ofKind(recs, "DATA")
	if len(data) < 3 {
		t.Fatalf("data %d", len(data))
	}
	for i, m := range msgs {
		b, _ := decodeBody(data[i])
		if !bytes.Equal(b, m) {
			t.Fatalf("frame %d not exact: %q", i, b)
		}
		if data[i]["src_seq"].(float64) != float64(7+i) {
			t.Fatalf("src_seq from tag 34: %v", data[i]["src_seq"])
		}
		env := data[i]["transport"].(map[string]any)
		if env["type"] != "tcp" || env["framing"] != "soh" || env["conn_id"].(float64) != 1 || env["frame_index"].(float64) != float64(i+1) {
			t.Fatalf("envelope %v", env)
		}
	}
	gaps := ofKind(recs, "GAP")
	if len(gaps) == 0 || gaps[0]["reason"] != "disconnect" {
		t.Fatalf("no disconnect gap: %v", gaps)
	}
	// the record after the disconnect GAP comes from connection 2
	found := false
	for _, r := range data {
		if r["transport"].(map[string]any)["conn_id"].(float64) == 2 {
			found = true
		}
	}
	if !found {
		t.Fatal("no data after reconnect")
	}
}

func TestTCPLen16Framing(t *testing.T) {
	l, _ := net.Listen("tcp", "127.0.0.1:0")
	defer l.Close()
	frames := [][]byte{len16([]byte("A\x00\xff")), len16(bytes.Repeat([]byte("Q"), 1000)), len16(nil), len16([]byte("end"))}
	var served atomic.Bool
	go func() {
		c, err := l.Accept()
		if err != nil {
			return
		}
		stream := bytes.Join(frames, nil)
		for i := 0; i < len(stream); i += 7 { // 7-byte writes: prefixes and payloads straddle reads
			end := i + 7
			if end > len(stream) {
				end = len(stream)
			}
			_, _ = c.Write(stream[i:end])
		}
		served.Store(true)
		time.Sleep(3 * time.Second)
		c.Close()
	}()
	root := runSource(t, SourceConfig{Name: "itch", Type: "tcp", Addr: l.Addr().String(), Framing: "len16"}, 5*time.Second,
		func() bool {
			if !served.Load() {
				return false
			}
			time.Sleep(100 * time.Millisecond)
			return true
		})
	recs := readAll(t, root, "itch")
	data := ofKind(recs, "DATA")
	if len(data) != len(frames) {
		t.Fatalf("got %d frames, want %d (gaps: %v)", len(data), len(frames), ofKind(recs, "GAP"))
	}
	for i, f := range frames {
		b, _ := decodeBody(data[i])
		if !bytes.Equal(b, f) {
			t.Fatalf("frame %d differs", i)
		}
	}
	// 0xff makes frame 0 non-UTF-8 → b64; "\x00\x03end" is valid UTF-8 → stored as a JSON string
	if data[0]["body_encoding"] != "b64" || data[3]["body_encoding"] != "utf8" {
		t.Fatalf("body encodings: %v %v", data[0]["body_encoding"], data[3]["body_encoding"])
	}
	if len(ofKind(recs, "GAP")) != 0 {
		t.Fatalf("unexpected gaps %v", ofKind(recs, "GAP"))
	}
}

func TestWebsocketTransport(t *testing.T) {
	up := websocket.Upgrader{}
	var conns atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		k := conns.Add(1)
		_, sub, _ := c.ReadMessage()
		_ = c.WriteMessage(websocket.TextMessage, []byte(`{"subscribed":"`+string(sub)+`"}`))
		_ = c.WriteMessage(websocket.BinaryMessage, []byte{0, 1, 2, 0xff})
		if k == 1 {
			return // hang up → disconnect GAP → reconnect
		}
		time.Sleep(3 * time.Second)
	}))
	defer srv.Close()
	url := "ws" + strings.TrimPrefix(srv.URL, "http")
	root := runSource(t, SourceConfig{Name: "ws", Type: "websocket", URL: url, SendOnConnect: []string{"SUB:ABC"}},
		5*time.Second, func() bool { return conns.Load() >= 2 })
	recs := readAll(t, root, "ws")
	data := ofKind(recs, "DATA")
	if len(data) < 4 {
		t.Fatalf("data %d", len(data))
	}
	b0, _ := decodeBody(data[0])
	b1, _ := decodeBody(data[1])
	if string(b0) != `{"subscribed":"SUB:ABC"}` || !bytes.Equal(b1, []byte{0, 1, 2, 0xff}) {
		t.Fatalf("%q %q", b0, b1)
	}
	if data[0]["transport"].(map[string]any)["opcode"] != "text" || data[1]["transport"].(map[string]any)["opcode"] != "binary" {
		t.Fatal("opcode")
	}
	gaps := ofKind(recs, "GAP")
	if len(gaps) == 0 || gaps[0]["reason"] != "disconnect" {
		t.Fatalf("gaps %v", gaps)
	}
}

func TestFileTailResumesAndDetectsTruncation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "feed.log")
	f, _ := os.Create(path)
	_, _ = f.WriteString("l1\nl2\n")
	f.Close()

	root := t.TempDir()
	st, _ := OpenStore(root, "cap", "test", 64)
	cfg := SourceConfig{Name: "tail", Type: "file_tail", Path: path, Framing: "line", PollMs: 20}
	tr, err := NewTransport(cfg, st)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { tr.Run(ctx); close(done) }()
	w := st.Writer("tail")
	waitFor := func(n int64) {
		deadline := time.Now().Add(3 * time.Second)
		for w.Enqueued.Load() < n && time.Now().Before(deadline) {
			time.Sleep(10 * time.Millisecond)
		}
	}
	waitFor(2)
	f, _ = os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	_, _ = f.WriteString("l3 part") // partial line: must not be emitted yet
	time.Sleep(80 * time.Millisecond)
	if w.Enqueued.Load() != 2 {
		t.Fatalf("partial line emitted: %d", w.Enqueued.Load())
	}
	_, _ = f.WriteString("ial\n")
	f.Close()
	waitFor(3)
	cancel()
	<-done
	_ = st.Close()
	recs := readAll(t, root, "tail")
	data := ofKind(recs, "DATA")
	if len(data) != 3 {
		t.Fatalf("data %d", len(data))
	}
	b, _ := decodeBody(data[2])
	if string(b) != "l3 partial\n" || data[2]["transport"].(map[string]any)["offset"].(float64) != 6 {
		t.Fatalf("%q %v", b, data[2]["transport"])
	}
	stRaw, err := os.ReadFile(filepath.Join(root, "state", "tail.offset.json"))
	if err != nil || !strings.Contains(string(stRaw), `"offset":17`) {
		t.Fatalf("offset state %s %v", stRaw, err)
	}

	// restart against the same store: only new lines are captured (resume from persisted offset)
	f, _ = os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	_, _ = f.WriteString("l4\n")
	f.Close()
	st2, _ := OpenStore(root, "cap", "test", 64)
	tr2, _ := NewTransport(cfg, st2)
	ctx2, cancel2 := context.WithCancel(context.Background())
	done2 := make(chan struct{})
	go func() { tr2.Run(ctx2); close(done2) }()
	w2 := st2.Writer("tail")
	for w2.Enqueued.Load() < 1 && time.Now().Before(time.Now().Add(2*time.Second)) {
		time.Sleep(10 * time.Millisecond)
	}
	// truncate the file → GAP{reason:"truncate"} and a restart from offset 0
	_ = os.WriteFile(path, []byte("fresh\n"), 0o644)
	deadline := time.Now().Add(3 * time.Second)
	for w2.Enqueued.Load() < 3 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	cancel2()
	<-done2
	_ = st2.Close()
	recs = readAll(t, root, "tail")
	var after []map[string]any
	for _, r := range recs {
		if r["epoch"] == st2.Epoch && (r["kind"] == "DATA" || r["kind"] == "GAP") {
			after = append(after, r)
		}
	}
	if len(after) != 3 {
		t.Fatalf("after restart: %v", after)
	}
	b, _ = decodeBody(after[0])
	if string(b) != "l4\n" || after[1]["reason"] != "truncate" {
		t.Fatalf("resume/truncate: %q %v", b, after[1]["reason"])
	}
	b, _ = decodeBody(after[2])
	if string(b) != "fresh\n" || after[2]["transport"].(map[string]any)["offset"].(float64) != 0 {
		t.Fatalf("post-truncate: %q", b)
	}
	if _, ok, _ := VerifyStore(root); !ok {
		t.Fatal("verify")
	}
}

func TestFileTailLen16(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "itch.bin")
	frames := [][]byte{len16([]byte("one")), len16([]byte{0xff, 0x00}), len16(bytes.Repeat([]byte("x"), 70000/10))}
	_ = os.WriteFile(path, bytes.Join(frames, nil), 0o644)
	root := runSource(t, SourceConfig{Name: "bin", Type: "file_tail", Path: path, Framing: "len16", PollMs: 20},
		2*time.Second, nil)
	data := ofKind(readAll(t, root, "bin"), "DATA")
	if len(data) != 3 {
		t.Fatalf("data %d", len(data))
	}
	for i, fr := range frames {
		b, _ := decodeBody(data[i])
		if !bytes.Equal(b, fr) {
			t.Fatalf("frame %d", i)
		}
	}
}

func TestDaemonRunWithHeartbeatAndRunnerMeta(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("ok")) }))
	defer srv.Close()
	out := t.TempDir()
	cfg := Config{Out: out, CapturerID: "unit", HeartbeatMs: 100, Sources: []SourceConfig{
		{Name: "a", Type: "http_poll", URL: srv.URL, IntervalMs: 40}}}
	d, err := NewDaemon(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 450*time.Millisecond)
	defer cancel()
	if err := d.Run(ctx); err != nil {
		t.Fatal(err)
	}
	hb := ofKind(readAll(t, out, "heartbeat"), "HEARTBEAT")
	if len(hb) < 4 {
		t.Fatalf("heartbeats %d", len(hb))
	}
	last := hb[len(hb)-1]["status"].(map[string]any)
	counts := last["counts"].(map[string]any)["a"].(map[string]any)
	if counts["ok"].(float64) < 5 || counts["written"].(float64) < 5 || counts["dropped"].(float64) != 0 {
		t.Fatalf("counts %v", counts)
	}
	if age, ok := last["ages_s"].(map[string]any)["a"].(float64); !ok || age < 0 || age > 5 {
		t.Fatalf("ages %v", last["ages_s"])
	}
	meta := ofKind(readAll(t, out, "runner"), "META")
	if len(meta) != 3 || meta[1]["started"] != true || meta[2]["finished"] != true { // segment META + started + finished
		t.Fatalf("runner meta %d", len(meta))
	}
	if _, err := os.Stat(filepath.Join(out, "status.json")); err != nil {
		t.Fatal("status.json")
	}
	reports, ok, _ := VerifyStore(out)
	if !ok || len(reports) != 3 {
		t.Fatalf("verify %v", reports)
	}
	fmt.Println("daemon segments:", len(reports))
}
