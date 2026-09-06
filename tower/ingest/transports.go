// Transports: each source runs one goroutine that receives bytes and hands
// Records to the source's SourceWriter. Transports never touch files.
//
// Loss accounting rules (shared by every transport):
//
//   - Streaming transports (tcp, websocket) use SourceWriter.Offer: a full
//     queue drops the frame, the drop is counted, and the writer emits
//     GAP{reason:"drop", dropped:N}. Polling/tailing transports (http_poll,
//     file_tail) use the blocking Put — the upstream can wait, so nothing is dropped.
//   - A connection that was established and then fails yields
//     GAP{reason:"disconnect"}; a failed connection attempt yields
//     GAP{reason:"connect_error"}; both are followed by reconnection with
//     exponential backoff (1 s doubling to 60 s, reset once a connection has
//     delivered a frame).
//   - Bytes a framer had to skip to resynchronise yield GAP{reason:"resync"}.
//   - HTTP non-2xx responses are GAP{reason:"http"} carrying the envelope and
//     the body; transport exceptions are GAP{reason:"exception"}.
//   - file_tail truncation/rotation yield GAP{reason:"truncate"/"rotate"}.
//
// Receipt clocks are read when the bytes arrive (t_recv_utc/t_recv_mono_ns),
// not when the record is written.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptrace"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
)

// SourceConfig is one entry of the config's "sources" list.
type SourceConfig struct {
	Name string `json:"name"`
	Type string `json:"type"` // http_poll | websocket | tcp | file_tail

	// http_poll
	URL         string            `json:"url"`
	Method      string            `json:"method"`
	Headers     map[string]string `json:"headers"`
	Form        map[string]string `json:"form"`
	Params      map[string]string `json:"params"`
	IntervalMs  int               `json:"interval_ms"`
	Key         *string           `json:"key"`
	TokenURL    string            `json:"token_url"`
	TokenRegex  string            `json:"token_regex"`
	TokenHeader string            `json:"token_header"`
	TokenTTLs   float64           `json:"token_ttl_s"`
	TimeoutMs   int               `json:"timeout_ms"`
	TLSInsecure bool              `json:"tls_insecure"`

	// websocket / tcp
	SendOnConnect []string `json:"send_on_connect"`
	PingMs        int      `json:"ping_ms"`
	Addr          string   `json:"addr"`
	Framing       string   `json:"framing"` // line | soh | len16
	Delimiter     string   `json:"delimiter"`
	ReadTimeoutMs int      `json:"read_timeout_ms"`

	// file_tail
	Path    string `json:"path"`
	PollMs  int    `json:"poll_ms"`
	FromEnd bool   `json:"from_end"` // first run only (no persisted offset): start at the current end of the file (tail -f); the skipped prefix is recorded as GAP{from_end}
}

// Transport is a running source.
type Transport interface {
	Name() string
	Run(ctx context.Context)
	Status() Obj
}

// counters shared by all transports; read by the heartbeat.
type counters struct {
	ok, err, unchanged, frames, reconnects atomic.Int64
	state                                  atomic.Value // string
}

func (c *counters) setState(s string) { c.state.Store(s) }

func (c *counters) status(w *SourceWriter) Obj {
	st, _ := c.state.Load().(string)
	return Obj{
		{"ok", c.ok.Load()}, {"err", c.err.Load()}, {"unchanged", c.unchanged.Load()},
		{"frames", c.frames.Load()}, {"reconnects", c.reconnects.Load()},
		{"enqueued", w.Enqueued.Load()}, {"written", w.Written.Load()}, {"dropped", w.Dropped.Load()},
		{"queue", w.QueueLen()}, {"bytes", w.DataBytes.Load()}, {"state", st},
	}
}

// NewTransport builds the transport for a source config.
func NewTransport(cfg SourceConfig, store *Store) (Transport, error) {
	if cfg.Name == "" {
		return nil, errors.New("source without name")
	}
	if strings.Contains(cfg.Name, "__") || strings.ContainsAny(cfg.Name, "/\\ ") {
		return nil, fmt.Errorf("source name %q: no '__', slashes or spaces (segment file naming)", cfg.Name)
	}
	w := store.Writer(cfg.Name)
	switch cfg.Type {
	case "http_poll":
		return newHTTPPoll(cfg, w)
	case "websocket":
		return newWebsocket(cfg, w)
	case "tcp":
		return newTCP(cfg, w)
	case "file_tail":
		return newFileTail(cfg, w, store.Root)
	}
	return nil, fmt.Errorf("source %s: unknown type %q", cfg.Name, cfg.Type)
}

// backoff is the reconnect schedule: 1 s doubling to 60 s.
type backoff struct{ cur time.Duration }

func (b *backoff) next() time.Duration {
	if b.cur == 0 {
		b.cur = time.Second
	} else {
		b.cur *= 2
		if b.cur > 60*time.Second {
			b.cur = 60 * time.Second
		}
	}
	return b.cur
}
func (b *backoff) reset() { b.cur = 0 }

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

// ---------------------------------------------------------------- http_poll

type httpPoll struct {
	cfg    SourceConfig
	w      *SourceWriter
	client *http.Client
	c      counters

	tokenRe      *regexp.Regexp
	token        string
	tokenMono    time.Time
	tokenRefresh atomic.Int64
	lastSHA      string
}

func newHTTPPoll(cfg SourceConfig, w *SourceWriter) (*httpPoll, error) {
	if cfg.URL == "" {
		return nil, fmt.Errorf("source %s: http_poll needs url", cfg.Name)
	}
	if cfg.Method == "" {
		if len(cfg.Form) > 0 {
			cfg.Method = "POST"
		} else {
			cfg.Method = "GET"
		}
	}
	if cfg.IntervalMs <= 0 {
		cfg.IntervalMs = 1000
	}
	if cfg.TimeoutMs <= 0 {
		cfg.TimeoutMs = 20000
	}
	if cfg.TokenHeader == "" {
		cfg.TokenHeader = "RequestVerificationToken"
	}
	if cfg.TokenTTLs <= 0 {
		cfg.TokenTTLs = 1500
	}
	p := &httpPoll{cfg: cfg, w: w}
	if cfg.TokenRegex != "" {
		re, err := regexp.Compile(cfg.TokenRegex)
		if err != nil {
			return nil, fmt.Errorf("source %s: token_regex: %w", cfg.Name, err)
		}
		if re.NumSubexp() < 1 {
			return nil, fmt.Errorf("source %s: token_regex needs one capture group", cfg.Name)
		}
		p.tokenRe = re
		if cfg.TokenURL == "" {
			return nil, fmt.Errorf("source %s: token_regex needs token_url", cfg.Name)
		}
	}
	jar, _ := cookiejar.New(nil)
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.TLSClientConfig = &tls.Config{InsecureSkipVerify: cfg.TLSInsecure} //nolint:gosec — explicit opt-in per source
	p.client = &http.Client{Jar: jar, Transport: tr, Timeout: time.Duration(cfg.TimeoutMs) * time.Millisecond}
	p.c.setState("idle")
	return p, nil
}

func (p *httpPoll) Name() string { return p.cfg.Name }

func (p *httpPoll) Status() Obj {
	o := p.c.status(p.w)
	o = append(o, KV{"token_refreshes", p.tokenRefresh.Load()})
	return o
}

// fetched is one HTTP exchange: the envelope and the raw body.
type fetched struct {
	env    Obj
	status int
	body   []byte
	err    error
}

// do performs one request and fills the seeing-style http envelope
// (method, url, params, form, request_headers, t_send_utc, tls_verify,
// t_first_byte_utc, t_last_byte_utc, status, response_headers, final_url, elapsed_ms).
func (p *httpPoll) do(ctx context.Context, method, rawURL string, headers map[string]string,
	form map[string]string, params map[string]string) fetched {
	u, err := url.Parse(rawURL)
	if err != nil {
		return fetched{err: err, env: Obj{{"method", method}, {"url", rawURL}, {"exception", err.Error()}}}
	}
	if len(params) > 0 {
		q := u.Query()
		for k, v := range params {
			q.Set(k, v)
		}
		u.RawQuery = q.Encode()
	}
	var bodyReader io.Reader
	if len(form) > 0 {
		vals := url.Values{}
		for k, v := range form {
			vals.Set(k, v)
		}
		bodyReader = strings.NewReader(vals.Encode())
	}
	req, err := http.NewRequestWithContext(ctx, method, u.String(), bodyReader)
	if err != nil {
		return fetched{err: err, env: Obj{{"method", method}, {"url", rawURL}, {"exception", err.Error()}}}
	}
	hdrs := map[string]string{"User-Agent": "tower-ingest/0.1 (personal research; polite)", "Accept": "*/*",
		"Accept-Language": "en-US,en;q=0.9"}
	if bodyReader != nil {
		hdrs["Content-Type"] = "application/x-www-form-urlencoded"
	}
	for k, v := range headers {
		hdrs[k] = v
	}
	for k, v := range hdrs {
		req.Header.Set(k, v)
	}
	reqHdr := Obj{}
	for _, k := range sortedKeys(hdrs) {
		if strings.EqualFold(k, "cookie") {
			continue
		}
		reqHdr = append(reqHdr, KV{k, hdrs[k]})
	}
	var paramsV, formV any
	if len(params) > 0 {
		paramsV = mapObj(params)
	}
	if len(form) > 0 {
		formV = mapObj(form)
	}
	env := Obj{{"method", method}, {"url", rawURL}, {"params", paramsV}, {"form", formV},
		{"request_headers", reqHdr}, {"t_send_utc", isoUTC(time.Now())}, {"tls_verify", !p.cfg.TLSInsecure}}
	var firstByte time.Time
	trace := &httptrace.ClientTrace{GotFirstResponseByte: func() { firstByte = time.Now() }}
	req = req.WithContext(httptrace.WithClientTrace(req.Context(), trace))
	start := time.Now()
	resp, err := p.client.Do(req)
	if err != nil {
		env = append(env, KV{"t_last_byte_utc", isoUTC(time.Now())},
			KV{"exception", fmt.Sprintf("%T: %s", err, truncate(err.Error(), 500))})
		return fetched{err: err, env: env}
	}
	defer resp.Body.Close()
	if firstByte.IsZero() {
		firstByte = time.Now()
	}
	body, rerr := io.ReadAll(resp.Body)
	last := time.Now()
	respHdr := Obj{}
	keys := make([]string, 0, len(resp.Header))
	for k := range resp.Header {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		respHdr = append(respHdr, KV{k, strings.Join(resp.Header[k], ", ")})
	}
	env = append(env, KV{"t_first_byte_utc", isoUTC(firstByte)}, KV{"t_last_byte_utc", isoUTC(last)},
		KV{"status", resp.StatusCode}, KV{"response_headers", respHdr},
		KV{"final_url", resp.Request.URL.String()}, KV{"elapsed_ms", int(last.Sub(start) / time.Millisecond)})
	if rerr != nil {
		env = append(env, KV{"exception", fmt.Sprintf("%T: %s", rerr, truncate(rerr.Error(), 500))})
		return fetched{err: rerr, env: env, status: resp.StatusCode, body: body}
	}
	return fetched{env: env, status: resp.StatusCode, body: body}
}

// ensureToken fetches token_url and extracts the token with token_regex
// (LankaBD anti-forgery flow: the token page also sets the cookies the jar
// will send with every subsequent request). Refreshed after token_ttl_s or on demand.
func (p *httpPoll) ensureToken(ctx context.Context, force bool) (bool, Obj) {
	if p.tokenRe == nil {
		return true, nil
	}
	if !force && p.token != "" && time.Since(p.tokenMono) < time.Duration(p.cfg.TokenTTLs*float64(time.Second)) {
		return true, nil
	}
	f := p.do(ctx, "GET", p.cfg.TokenURL, map[string]string{"Accept": "text/html,application/xhtml+xml"}, nil, nil)
	p.tokenRefresh.Add(1)
	if f.err == nil && f.status >= 200 && f.status < 300 {
		if m := p.tokenRe.FindSubmatch(f.body); m != nil {
			p.token = string(m[1])
			p.tokenMono = time.Now()
			return true, f.env
		}
		f.env = append(f.env, KV{"token_error", "token_regex did not match the token page"})
		return false, f.env
	}
	return false, f.env
}

func (p *httpPoll) headers() map[string]string {
	h := map[string]string{}
	for k, v := range p.cfg.Headers {
		h[k] = v
	}
	if p.tokenRe != nil {
		h[p.cfg.TokenHeader] = p.token
	}
	return h
}

// pollOnce is one poll cycle: token (if configured), request, retry once on
// a token-stale status (400/401/403/405), then DATA or GAP.
func (p *httpPoll) pollOnce(ctx context.Context) {
	var key any
	if p.cfg.Key != nil {
		key = *p.cfg.Key
	}
	ok, tenv := p.ensureToken(ctx, false)
	if !ok {
		p.c.err.Add(1)
		p.w.Put(GapRecord("token", "anti-forgery token unavailable", key, "http", tenv, nil))
		return
	}
	f := p.do(ctx, p.cfg.Method, p.cfg.URL, p.headers(), p.cfg.Form, p.cfg.Params)
	if p.tokenRe != nil && f.err == nil && (f.status == 400 || f.status == 401 || f.status == 403 || f.status == 405) {
		if ok, _ := p.ensureToken(ctx, true); ok {
			f = p.do(ctx, p.cfg.Method, p.cfg.URL, p.headers(), p.cfg.Form, p.cfg.Params)
			f.env = append(f.env, KV{"token_refreshed", true})
		}
	}
	if ctx.Err() != nil && f.err != nil {
		return // shutdown cut the request: nothing was received, nothing to claim
	}
	if f.err != nil {
		p.c.err.Add(1)
		p.c.setState("error")
		p.w.Put(GapRecord("exception", f.err.Error(), key, "http", f.env, nil))
		return
	}
	if f.status < 200 || f.status >= 300 {
		p.c.err.Add(1)
		p.c.setState("error")
		p.w.Put(GapRecord("http", fmt.Sprintf("http %d", f.status), key, "http", f.env, f.body))
		return
	}
	p.c.ok.Add(1)
	p.c.frames.Add(1)
	p.c.setState("ok")
	sha := sha256Hex(f.body)
	if sha == p.lastSHA {
		p.c.unchanged.Add(1) // counted only; the record is still written — nothing is deduplicated
	}
	p.lastSHA = sha
	p.w.Put(DataRecord(key, nil, "http", f.env, f.body))
}

func (p *httpPoll) Run(ctx context.Context) {
	interval := time.Duration(p.cfg.IntervalMs) * time.Millisecond
	for {
		p.pollOnce(ctx)
		if !sleepCtx(ctx, interval) {
			return
		}
	}
}

// ---------------------------------------------------------------- websocket

type wsTransport struct {
	cfg SourceConfig
	w   *SourceWriter
	c   counters
}

func newWebsocket(cfg SourceConfig, w *SourceWriter) (*wsTransport, error) {
	if cfg.URL == "" {
		return nil, fmt.Errorf("source %s: websocket needs url", cfg.Name)
	}
	if cfg.PingMs <= 0 {
		cfg.PingMs = 20000
	}
	t := &wsTransport{cfg: cfg, w: w}
	t.c.setState("disconnected")
	return t, nil
}

func (t *wsTransport) Name() string { return t.cfg.Name }
func (t *wsTransport) Status() Obj  { return t.c.status(t.w) }

func (t *wsTransport) Run(ctx context.Context) {
	var bo backoff
	connID := 0
	for ctx.Err() == nil {
		connID++
		hdr := http.Header{}
		for k, v := range t.cfg.Headers {
			hdr.Set(k, v)
		}
		dialer := websocket.Dialer{HandshakeTimeout: 15 * time.Second,
			TLSClientConfig: &tls.Config{InsecureSkipVerify: t.cfg.TLSInsecure}} //nolint:gosec
		conn, resp, err := dialer.DialContext(ctx, t.cfg.URL, hdr)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			t.c.err.Add(1)
			env := Obj{{"type", "websocket"}, {"url", t.cfg.URL}, {"conn_id", connID}}
			if resp != nil {
				env = append(env, KV{"status", resp.StatusCode})
			}
			t.w.Put(GapRecord("connect_error", err.Error(), nil, "transport", env, nil))
			if !sleepCtx(ctx, bo.next()) {
				return
			}
			continue
		}
		t.c.setState("connected")
		if connID > 1 {
			t.c.reconnects.Add(1)
		}
		err = t.serve(ctx, conn, connID, &bo)
		conn.Close()
		t.c.setState("disconnected")
		if ctx.Err() != nil {
			return
		}
		t.c.err.Add(1)
		env := Obj{{"type", "websocket"}, {"url", t.cfg.URL}, {"conn_id", connID}}
		t.w.Put(GapRecord("disconnect", errString(err), nil, "transport", env, nil))
		if !sleepCtx(ctx, bo.next()) {
			return
		}
	}
}

func (t *wsTransport) serve(ctx context.Context, conn *websocket.Conn, connID int, bo *backoff) error {
	var wmu sync.Mutex
	for _, m := range t.cfg.SendOnConnect {
		wmu.Lock()
		err := conn.WriteMessage(websocket.TextMessage, []byte(m))
		wmu.Unlock()
		if err != nil {
			return fmt.Errorf("send_on_connect: %w", err)
		}
	}
	stop := make(chan struct{})
	defer close(stop)
	go func() {
		tick := time.NewTicker(time.Duration(t.cfg.PingMs) * time.Millisecond)
		defer tick.Stop()
		for {
			select {
			case <-stop:
				return
			case <-ctx.Done():
				conn.Close() // unblocks ReadMessage
				return
			case <-tick.C:
				wmu.Lock()
				_ = conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second))
				wmu.Unlock()
			}
		}
	}()
	frame := 0
	for {
		op, data, err := conn.ReadMessage()
		if err != nil {
			return err
		}
		frame++
		t.c.frames.Add(1)
		t.c.ok.Add(1)
		bo.reset()
		opcode := "binary"
		if op == websocket.TextMessage {
			opcode = "text"
		}
		env := Obj{{"type", "websocket"}, {"url", t.cfg.URL}, {"conn_id", connID}, {"frame_index", frame}, {"opcode", opcode}}
		t.w.Offer(DataRecord(nil, nil, "transport", env, data))
	}
}

// ---------------------------------------------------------------- tcp

type tcpTransport struct {
	cfg SourceConfig
	w   *SourceWriter
	c   counters
}

func newTCP(cfg SourceConfig, w *SourceWriter) (*tcpTransport, error) {
	if cfg.Addr == "" {
		return nil, fmt.Errorf("source %s: tcp needs addr", cfg.Name)
	}
	if cfg.Framing == "" {
		cfg.Framing = "line"
	}
	if _, err := NewFramer(cfg.Framing, bytes.NewReader(nil), cfg.Delimiter); err != nil {
		return nil, fmt.Errorf("source %s: %w", cfg.Name, err)
	}
	t := &tcpTransport{cfg: cfg, w: w}
	t.c.setState("disconnected")
	return t, nil
}

func (t *tcpTransport) Name() string { return t.cfg.Name }
func (t *tcpTransport) Status() Obj  { return t.c.status(t.w) }

func (t *tcpTransport) Run(ctx context.Context) {
	var bo backoff
	connID := 0
	for ctx.Err() == nil {
		connID++
		d := net.Dialer{Timeout: 15 * time.Second}
		conn, err := d.DialContext(ctx, "tcp", t.cfg.Addr)
		env := Obj{{"type", "tcp"}, {"addr", t.cfg.Addr}, {"framing", t.cfg.Framing}, {"conn_id", connID}}
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			t.c.err.Add(1)
			t.w.Put(GapRecord("connect_error", err.Error(), nil, "transport", env, nil))
			if !sleepCtx(ctx, bo.next()) {
				return
			}
			continue
		}
		t.c.setState("connected")
		if connID > 1 {
			t.c.reconnects.Add(1)
		}
		err = t.serve(ctx, conn, connID, &bo)
		conn.Close()
		t.c.setState("disconnected")
		if ctx.Err() != nil {
			return
		}
		t.c.err.Add(1)
		t.w.Put(GapRecord("disconnect", errString(err), nil, "transport", env, nil))
		if !sleepCtx(ctx, bo.next()) {
			return
		}
	}
}

func (t *tcpTransport) serve(ctx context.Context, conn net.Conn, connID int, bo *backoff) error {
	for _, m := range t.cfg.SendOnConnect {
		if _, err := conn.Write([]byte(m)); err != nil {
			return fmt.Errorf("send_on_connect: %w", err)
		}
	}
	stop := make(chan struct{})
	defer close(stop)
	go func() {
		select {
		case <-stop:
		case <-ctx.Done():
			conn.Close()
		}
	}()
	fr, _ := NewFramer(t.cfg.Framing, conn, t.cfg.Delimiter)
	frame := 0
	for {
		if t.cfg.ReadTimeoutMs > 0 {
			_ = conn.SetReadDeadline(time.Now().Add(time.Duration(t.cfg.ReadTimeoutMs) * time.Millisecond))
		}
		data, err := fr.Next()
		if skipped := fr.Skipped(); skipped > 0 {
			env := Obj{{"type", "tcp"}, {"addr", t.cfg.Addr}, {"framing", t.cfg.Framing}, {"conn_id", connID}}
			t.w.Offer(GapRecord("resync", fmt.Sprintf("%d byte(s) skipped to resynchronise framing", skipped),
				nil, "transport", env, nil))
		}
		if err != nil {
			// Whatever ended the connection (clean cut, reset, read deadline,
			// shutdown), bytes read but not framed are never dropped silently.
			if partial := fr.Partial(); len(partial) > 0 {
				env := Obj{{"type", "tcp"}, {"addr", t.cfg.Addr}, {"framing", t.cfg.Framing}, {"conn_id", connID}}
				t.w.Put(GapRecord("partial_frame", fmt.Sprintf("connection ended inside a frame (%s); %d byte(s) kept in body",
					err.Error(), len(partial)), nil, "transport", env, append([]byte(nil), partial...)))
			}
			return err
		}
		frame++
		t.c.frames.Add(1)
		t.c.ok.Add(1)
		bo.reset()
		var srcSeq any
		if t.cfg.Framing == "soh" {
			if v := fixTag(data, "34"); v != "" {
				if n, err := strconv.ParseInt(v, 10, 64); err == nil {
					srcSeq = n
				} else {
					srcSeq = v
				}
			}
		}
		env := Obj{{"type", "tcp"}, {"addr", t.cfg.Addr}, {"framing", t.cfg.Framing}, {"conn_id", connID}, {"frame_index", frame}}
		t.w.Offer(DataRecord(nil, srcSeq, "transport", env, data))
	}
}

// ---------------------------------------------------------------- file_tail

type fileTail struct {
	cfg       SourceConfig
	w         *SourceWriter
	c         counters
	statePath string
}

type tailState struct {
	Path   string `json:"path"`
	Offset int64  `json:"offset"`
	Inode  uint64 `json:"inode"`
}

func newFileTail(cfg SourceConfig, w *SourceWriter, root string) (*fileTail, error) {
	if cfg.Path == "" {
		return nil, fmt.Errorf("source %s: file_tail needs path", cfg.Name)
	}
	if cfg.Framing == "" {
		cfg.Framing = "line"
	}
	if _, err := NewFramer(cfg.Framing, bytes.NewReader(nil), cfg.Delimiter); err != nil {
		return nil, fmt.Errorf("source %s: %w", cfg.Name, err)
	}
	if cfg.PollMs <= 0 {
		cfg.PollMs = 200
	}
	t := &fileTail{cfg: cfg, w: w, statePath: filepath.Join(root, "state", cfg.Name+".offset.json")}
	t.c.setState("idle")
	return t, nil
}

func (t *fileTail) Name() string { return t.cfg.Name }
func (t *fileTail) Status() Obj  { return t.c.status(t.w) }

func inodeOf(fi os.FileInfo) uint64 {
	if st, ok := fi.Sys().(*syscall.Stat_t); ok {
		return uint64(st.Ino)
	}
	return 0
}

func (t *fileTail) loadState() tailState {
	var st tailState
	if raw, err := os.ReadFile(t.statePath); err == nil {
		_ = json.Unmarshal(raw, &st)
	}
	return st
}

func (t *fileTail) saveState(st tailState) error {
	if err := os.MkdirAll(filepath.Dir(t.statePath), 0o755); err != nil {
		return err
	}
	raw, _ := json.Marshal(st)
	tmp := t.statePath + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, t.statePath)
}

// tailReader is an io.Reader over a growing file: at EOF it waits poll_ms and
// retries; it reports truncation and rotation as errors so the caller can
// reset framing and record a GAP.
type tailReader struct {
	ctx    context.Context
	path   string
	f      *os.File
	inode  uint64
	pos    int64
	poll   time.Duration
	reason error
}

var (
	errTruncated = errors.New("file truncated")
	errRotated   = errors.New("file rotated")
)

func (r *tailReader) Read(p []byte) (int, error) {
	for {
		if r.f != nil {
			n, err := r.f.Read(p)
			if n > 0 {
				r.pos += int64(n)
				return n, nil
			}
			if err != nil && !errors.Is(err, io.EOF) {
				return 0, err
			}
			// at EOF: check truncation / rotation
			if fi, err := r.f.Stat(); err == nil && fi.Size() < r.pos {
				r.pos = 0
				_, _ = r.f.Seek(0, io.SeekStart)
				return 0, errTruncated
			}
			if fi, err := os.Stat(r.path); err == nil && inodeOf(fi) != r.inode && inodeOf(fi) != 0 {
				// rotated: report it now even if the new file cannot be opened
				// yet (Read keeps retrying open(0)); the caller resets framing
				// so old-file bytes are never merged into a new-file frame.
				r.reopen(0)
				return 0, errRotated
			}
		} else if err := r.open(r.pos); err != nil && r.f == nil {
			// file absent: keep waiting
		}
		if r.ctx.Err() != nil {
			return 0, r.ctx.Err()
		}
		if !sleepCtx(r.ctx, r.poll) {
			return 0, r.ctx.Err()
		}
	}
}

func (r *tailReader) open(offset int64) error {
	f, err := os.Open(r.path)
	if err != nil {
		return err
	}
	fi, err := f.Stat()
	if err != nil {
		f.Close()
		return err
	}
	if offset > fi.Size() {
		offset = 0
	}
	if _, err := f.Seek(offset, io.SeekStart); err != nil {
		f.Close()
		return err
	}
	r.f, r.inode, r.pos = f, inodeOf(fi), offset
	return nil
}

// reopen closes the current file and opens the path again at offset; when
// the open fails the reader is left closed with pos = offset so Read retries
// from there on its next poll.
func (r *tailReader) reopen(offset int64) {
	if r.f != nil {
		r.f.Close()
		r.f, r.inode = nil, 0
	}
	r.pos = offset
	_ = r.open(offset)
}

// startOffset decides where a run begins and why; a non-empty reason is
// recorded as a GAP so a skipped or restarted prefix is never silent.
func (t *fileTail) startOffset(st tailState) (start int64, reason, detail string) {
	fi, statErr := os.Stat(t.cfg.Path)
	resuming := st.Path == t.cfg.Path
	switch {
	case resuming && statErr == nil && st.Inode != 0 && inodeOf(fi) != st.Inode:
		return 0, "rotate", fmt.Sprintf("file replaced since the last run (inode %d -> %d); restarting at offset 0 (persisted offset %d)",
			st.Inode, inodeOf(fi), st.Offset)
	case resuming && statErr == nil && fi.Size() < st.Offset:
		return 0, "truncate", fmt.Sprintf("file shorter (%d) than the persisted offset (%d); restarting at offset 0", fi.Size(), st.Offset)
	case resuming:
		return st.Offset, "", ""
	case t.cfg.FromEnd && statErr == nil && fi.Size() > 0:
		return fi.Size(), "from_end", fmt.Sprintf("from_end: %d byte(s) present before the first run are not captured", fi.Size())
	}
	return 0, "", ""
}

func (t *fileTail) Run(ctx context.Context) {
	start, reason, detail := t.startOffset(t.loadState())
	rd := &tailReader{ctx: ctx, path: t.cfg.Path, poll: time.Duration(t.cfg.PollMs) * time.Millisecond}
	if err := rd.open(start); err != nil {
		rd.pos = start
	}
	newFramer := func() (*bufio.Reader, Framer) {
		br := bufio.NewReaderSize(rd, 1<<16)
		fr, _ := NewFramer(t.cfg.Framing, br, t.cfg.Delimiter)
		return br, fr
	}
	br, fr := newFramer()
	lastSave := time.Now()
	frameStart := rd.pos
	envBase := func() Obj {
		return Obj{{"type", "file_tail"}, {"path", t.cfg.Path}, {"framing", t.cfg.Framing}}
	}
	persist := func() {
		_ = t.saveState(tailState{Path: t.cfg.Path, Offset: frameStart, Inode: rd.inode})
	}
	defer persist()
	if reason != "" {
		env := envBase()
		env = append(env, KV{"offset", frameStart})
		t.w.Put(GapRecord(reason, detail, nil, "transport", env, nil))
	}
	persist()
	t.c.setState("tailing")
	for {
		data, err := fr.Next()
		if err != nil {
			switch {
			case errors.Is(err, errTruncated), errors.Is(err, errRotated):
				reason := "truncate"
				if errors.Is(err, errRotated) {
					reason = "rotate"
				}
				partial := fr.Partial()
				env := envBase()
				env = append(env, KV{"offset", frameStart})
				var body []byte
				if len(partial) > 0 {
					body = append([]byte(nil), partial...)
				}
				t.w.Put(GapRecord(reason, fmt.Sprintf("%s: restarting at offset 0 (%d unframed byte(s) kept in body)",
					err.Error(), len(partial)), nil, "transport", env, body))
				t.c.err.Add(1)
				br, fr = newFramer()
				frameStart = 0
				persist()
				continue
			case ctx.Err() != nil:
				return
			default:
				// an I/O error: record it, then re-open at the consumed offset
				// with fresh framing — the unframed tail is re-read from the
				// file, so nothing is lost or duplicated and a dead descriptor
				// cannot make the loop spin on the same error.
				t.c.err.Add(1)
				t.c.setState("error")
				env := envBase()
				env = append(env, KV{"offset", frameStart})
				t.w.Put(GapRecord("exception", fmt.Sprintf("%s; re-reading from offset %d", err.Error(), frameStart),
					nil, "transport", env, nil))
				if !sleepCtx(ctx, time.Duration(t.cfg.PollMs)*time.Millisecond) {
					return
				}
				rd.reopen(frameStart)
				br, fr = newFramer()
				continue
			}
		}
		env := envBase()
		env = append(env, KV{"offset", frameStart}, KV{"frame_index", t.c.frames.Load() + 1})
		t.c.frames.Add(1)
		t.c.ok.Add(1)
		t.w.Put(DataRecord(nil, nil, "transport", env, data))
		// consumed offset = bytes read from the file - bytes buffered in bufio - bytes held unframed
		frameStart = rd.pos - int64(br.Buffered()) - int64(len(fr.Partial()))
		if time.Since(lastSave) > time.Second {
			persist()
			lastSave = time.Now()
		}
	}
}

// ---------------------------------------------------------------- helpers

func sortedKeys(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func mapObj(m map[string]string) Obj {
	o := Obj{}
	for _, k := range sortedKeys(m) {
		o = append(o, KV{k, m[k]})
	}
	return o
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func errString(err error) string {
	if err == nil {
		return "closed"
	}
	return err.Error()
}
