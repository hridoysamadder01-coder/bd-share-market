package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"hash/crc32"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

var isoRe = regexp.MustCompile(`^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$`)

// readAll returns the records of every manifest segment of a source, in order.
func readAll(t *testing.T, root, source string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(root, "MANIFEST.json"))
	if err != nil {
		t.Fatal(err)
	}
	var man Manifest
	if err := json.Unmarshal(raw, &man); err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, s := range man.Segments {
		if s.Source != source {
			continue
		}
		recs, err := ReadSegment(filepath.Join(root, s.Path))
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, recs...)
	}
	return out
}

func ofKind(recs []map[string]any, kind string) []map[string]any {
	var out []map[string]any
	for _, r := range recs {
		if r["kind"] == kind {
			out = append(out, r)
		}
	}
	return out
}

func TestObjMarshalOrderAndNoHTMLEscape(t *testing.T) {
	o := Obj{{"z", 1}, {"a", "<b>&"}, {"m", nil}}
	b, err := marshalJSON(o)
	if err != nil {
		t.Fatal(err)
	}
	if string(b) != `{"z":1,"a":"<b>&","m":null}` {
		t.Fatalf("got %s", b)
	}
}

func TestIsoUTCMatchesPythonIsoformat(t *testing.T) {
	s := isoUTC(time.Date(2026, 9, 6, 1, 6, 50, 988888000, time.UTC))
	if s != "2026-09-06T01:06:50.988888+00:00" {
		t.Fatalf("got %s", s)
	}
	if !isoRe.MatchString(isoUTC(time.Now())) {
		t.Fatal("format")
	}
}

func TestSegmentRoundTrip(t *testing.T) {
	root := t.TempDir()
	st, err := OpenStore(root, "cap", "test", 16)
	if err != nil {
		t.Fatal(err)
	}
	w := st.Writer("src_a")
	bodies := [][]byte{
		[]byte(`{"a": 1, "sym": "টাকা", "html": "<b>&</b>"}` + "\n"),
		{0x00, 0x01, 0xff, 0xfe, 'x', '\n'}, // invalid UTF-8 → b64
		[]byte("line with \x00 nul and \t tab and \x1b esc"),
		{},
	}
	for i, b := range bodies {
		env := Obj{{"method", "GET"}, {"url", "http://x/" + strings.Repeat("y", i)}, {"status", 200}}
		w.Put(DataRecord("K", i, "http", env, b))
	}
	w.Put(GapRecord("http", "http 503", "K", "http", Obj{{"status", 503}}, []byte("<h1>503</h1>")))
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}

	// file naming
	files, _ := filepath.Glob(filepath.Join(root, "segments", "*.jsonl"))
	if len(files) != 1 {
		t.Fatalf("want 1 segment, got %v", files)
	}
	name := filepath.Base(files[0])
	nameRe := regexp.MustCompile(`^src_a__cap__[0-9a-f]{8}__\d{8}T\d\d__00000000\.jsonl$`)
	if !nameRe.MatchString(name) {
		t.Fatalf("bad segment name %s", name)
	}

	recs := readAll(t, root, "src_a")
	if len(recs) != 1+len(bodies)+1+1 { // META + DATA*4 + GAP + TRAILER
		t.Fatalf("got %d records", len(recs))
	}
	if recs[0]["kind"] != "META" || recs[0]["prev_segment_sha256"] != nil || recs[0]["seq"].(float64) != 0 {
		t.Fatalf("bad META %v", recs[0])
	}
	for i, r := range recs {
		if r["seq"].(float64) != float64(i) {
			t.Fatalf("seq not contiguous at %d: %v", i, r["seq"])
		}
		for _, k := range []string{"source", "capturer_id", "epoch", "t_recv_utc", "t_recv_mono_ns"} {
			if _, ok := r[k]; !ok {
				t.Fatalf("record %d lacks %s", i, k)
			}
		}
		if !isoRe.MatchString(r["t_recv_utc"].(string)) {
			t.Fatalf("bad t_recv_utc %v", r["t_recv_utc"])
		}
	}
	data := ofKind(recs, "DATA")
	for i, r := range data {
		body, err := decodeBody(r)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(body, bodies[i]) {
			t.Fatalf("body %d not byte-exact: %q vs %q", i, body, bodies[i])
		}
		wantEnc := "utf8"
		if i == 1 {
			wantEnc = "b64"
		}
		if r["body_encoding"] != wantEnc {
			t.Fatalf("body %d encoding %v", i, r["body_encoding"])
		}
		if uint32(r["crc32"].(float64)) != crc32.ChecksumIEEE(bodies[i]) {
			t.Fatalf("crc mismatch %d", i)
		}
		if r["body_sha256"] != sha256Hex(bodies[i]) || int(r["len"].(float64)) != len(bodies[i]) {
			t.Fatalf("sha/len mismatch %d", i)
		}
		if r["key"] != "K" || r["src_seq"].(float64) != float64(i) {
			t.Fatalf("key/src_seq %v %v", r["key"], r["src_seq"])
		}
		if r["http"].(map[string]any)["status"].(float64) != 200 {
			t.Fatalf("envelope lost")
		}
	}
	gap := ofKind(recs, "GAP")[0]
	if gap["reason"] != "http" || gap["body"] != "<h1>503</h1>" {
		t.Fatalf("bad gap %v", gap)
	}

	// trailer: records excludes the trailer; sha256_before_trailer over all preceding bytes
	tr := recs[len(recs)-1]
	if tr["kind"] != "TRAILER" || int(tr["records"].(float64)) != len(recs)-1 {
		t.Fatalf("bad trailer %v", tr)
	}
	raw, _ := os.ReadFile(files[0])
	lines := bytes.SplitAfter(raw, []byte("\n"))
	h := sha256.New()
	for _, l := range lines[:len(lines)-2] { // last element is "" after the final \n
		h.Write(l)
	}
	if tr["sha256_before_trailer"] != hex.EncodeToString(h.Sum(nil)) {
		t.Fatal("sha256_before_trailer mismatch")
	}
	if tr["first_seq"].(float64) != 0 || int(tr["last_seq"].(float64)) != len(recs)-2 {
		t.Fatalf("first/last seq %v %v", tr["first_seq"], tr["last_seq"])
	}

	// manifest
	mraw, _ := os.ReadFile(filepath.Join(root, "MANIFEST.json"))
	var man map[string]any
	if err := json.Unmarshal(mraw, &man); err != nil {
		t.Fatal(err)
	}
	if man["schema_version"].(float64) != 1 || man["capturer_id"] != "cap" || man["closed_utc"] == nil {
		t.Fatalf("bad manifest %v", man)
	}
	segs := man["segments"].([]any)
	if len(segs) != 1 {
		t.Fatal("manifest segments")
	}
	seg := segs[0].(map[string]any)
	digest, size, _ := sha256File(files[0])
	if seg["sha256"] != digest || int64(seg["bytes"].(float64)) != size || seg["path"] != "segments/"+name {
		t.Fatalf("manifest entry %v", seg)
	}
	// Python convention: the manifest counts the trailer line and ends at the trailer's seq
	if int(seg["records"].(float64)) != len(recs) || seg["first_seq"].(float64) != 0 ||
		seg["last_seq"].(float64) != tr["seq"].(float64) {
		t.Fatalf("manifest records/seq convention: %v (lines=%d trailer seq=%v)", seg, len(recs), tr["seq"])
	}
	reports, ok, err := VerifyStore(root)
	if err != nil || !ok || len(reports) != 1 {
		t.Fatalf("verify: %v %v %v", reports, ok, err)
	}
}

func TestHourlyRotationAndHashChain(t *testing.T) {
	root := t.TempDir()
	st, err := OpenStore(root, "cap", "test", 16)
	if err != nil {
		t.Fatal(err)
	}
	w := st.Writer("s")
	t0 := time.Date(2026, 9, 6, 4, 59, 59, 0, time.UTC)
	for i := 0; i < 3; i++ {
		r := DataRecord(nil, nil, "transport", Obj{{"type", "test"}}, []byte("a"))
		r.TRecv = t0.Add(time.Duration(i) * time.Second) // 04:59:59, 05:00:00, 05:00:01
		w.Put(r)
	}
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}
	files, _ := filepath.Glob(filepath.Join(root, "segments", "*.jsonl"))
	if len(files) != 2 {
		t.Fatalf("want 2 segments, got %v", files)
	}
	if !strings.Contains(filepath.Base(files[0]), "__20260906T04__00000000.jsonl") ||
		!strings.Contains(filepath.Base(files[1]), "__20260906T05__00000003.jsonl") {
		t.Fatalf("names %v", files)
	}
	recs := readAll(t, root, "s")
	// seg1: META(0) DATA(1) TRAILER(2); seg2: META(3) DATA(4) DATA(5) TRAILER(6)
	if len(recs) != 7 || recs[3]["kind"] != "META" || recs[3]["seq"].(float64) != 3 {
		t.Fatalf("records %d", len(recs))
	}
	sha1, _, _ := sha256File(files[0])
	if recs[3]["prev_segment_sha256"] != sha1 {
		t.Fatal("chain: second META must carry the first segment's sha256")
	}
	if _, ok, _ := VerifyStore(root); !ok {
		t.Fatal("verify")
	}

	// restart: a new epoch continues the chain from the manifest and never appends to old files
	st2, _ := OpenStore(root, "cap", "test", 16)
	if st2.Epoch == st.Epoch {
		t.Fatal("epoch must be fresh")
	}
	st2.Writer("s").Put(DataRecord(nil, nil, "transport", Obj{{"type", "test"}}, []byte("b")))
	if err := st2.Close(); err != nil {
		t.Fatal(err)
	}
	files, _ = filepath.Glob(filepath.Join(root, "segments", "*.jsonl"))
	if len(files) != 3 {
		t.Fatalf("want 3 segments after restart, got %d", len(files))
	}
	recs = readAll(t, root, "s")
	sha2, _, _ := sha256File(filepath.Join(root, "segments", recs[3]["source"].(string)+"__cap__"+st.Epoch[:8]+"__20260906T05__00000003.jsonl"))
	last := recs[7]
	if last["kind"] != "META" || last["prev_segment_sha256"] != sha2 || last["epoch"] != st2.Epoch || last["seq"].(float64) != 0 {
		t.Fatalf("restart META %v", last)
	}
	reports, ok, err := VerifyStore(root)
	if err != nil || !ok || len(reports) != 3 {
		t.Fatalf("verify after restart: %v", reports)
	}
	mraw, _ := os.ReadFile(filepath.Join(root, "MANIFEST.json"))
	var man Manifest
	_ = json.Unmarshal(mraw, &man)
	if len(man.PreviousEpochs) != 1 || man.PreviousEpochs[0]["epoch"] != st.Epoch {
		t.Fatalf("previous_epochs %v", man.PreviousEpochs)
	}
}

func TestDropIsCountedAndWrittenAsGap(t *testing.T) {
	root := t.TempDir()
	st, _ := OpenStore(root, "cap", "test", 2)
	// a writer whose goroutine has not started: the queue fills deterministically
	w := &SourceWriter{Source: "fast", store: st, ch: make(chan Record, 2), syncCh: make(chan struct{}, 1), done: make(chan struct{})}
	st.mu.Lock()
	st.writers["fast"] = w
	st.mu.Unlock()
	accepted := 0
	for i := 0; i < 5; i++ {
		if w.Offer(DataRecord(nil, i, "transport", Obj{{"type", "test"}}, []byte{byte('0' + i)})) {
			accepted++
		}
	}
	if accepted != 2 || w.Dropped.Load() != 3 {
		t.Fatalf("accepted %d dropped %d", accepted, w.Dropped.Load())
	}
	go w.run()
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}
	recs := readAll(t, root, "fast")
	gaps := ofKind(recs, "GAP")
	if len(gaps) != 1 || gaps[0]["reason"] != "drop" || gaps[0]["dropped"].(float64) != 3 {
		t.Fatalf("drop gap %v", gaps)
	}
	if len(ofKind(recs, "DATA")) != 2 {
		t.Fatal("accepted records lost")
	}
	if _, ok, _ := VerifyStore(root); !ok {
		t.Fatal("verify")
	}
}

func TestVerifyDetectsTampering(t *testing.T) {
	root := t.TempDir()
	st, _ := OpenStore(root, "cap", "test", 4)
	st.Writer("s").Put(DataRecord(nil, nil, "transport", Obj{{"type", "test"}}, []byte("hello")))
	_ = st.Close()
	files, _ := filepath.Glob(filepath.Join(root, "segments", "*.jsonl"))
	raw, _ := os.ReadFile(files[0])
	tampered := bytes.Replace(raw, []byte(`"body":"hello"`), []byte(`"body":"hellp"`), 1)
	if bytes.Equal(raw, tampered) {
		t.Fatal("fixture")
	}
	_ = os.WriteFile(files[0], tampered, 0o644)
	rep := VerifySegment(files[0])
	if rep.OK || rep.BadRecords == 0 || rep.TrailerMatches {
		t.Fatalf("tampering not detected: %+v", rep)
	}
}
