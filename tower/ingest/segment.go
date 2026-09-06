// Segment writer: append-only, hash-chained JSONL segments byte-compatible with
// seeing/capture/raw_store.py (RawStore).
//
// Rules (mirroring the Python store, which is the reference implementation):
//
//   - One record per line. Every record carries kind, source, capturer_id,
//     epoch, seq (monotonic per source within the epoch), t_recv_utc
//     (RFC3339 with microseconds and a "+00:00" suffix, exactly like Python's
//     datetime.isoformat()) and t_recv_mono_ns (process-monotonic nanoseconds).
//   - DATA/GAP bodies are stored exactly as received: valid UTF-8 as a JSON
//     string ("body_encoding":"utf8"), anything else base64 ("b64").
//     len, crc32 (IEEE) and body_sha256 are computed over the raw bytes.
//   - One segment per (source, UTC hour) named
//     <source>__<capturer>__<epoch8>__<YYYYMMDDTHH>__<seq8>.jsonl under
//     <out>/segments. The first record is META with prev_segment_sha256 (the
//     sha256 of the previous closed segment of that source — the hash chain;
//     null for the first segment of a source, continued across restarts from
//     the manifest). The last record is TRAILER with records (lines before the
//     trailer, META included), first_seq, last_seq and sha256_before_trailer
//     (sha256 of every byte of the file before the trailer line). The trailer
//     consumes a seq number, as in Python.
//   - fdatasync at every heartbeat, GAP and segment close.
//   - MANIFEST.json {schema_version:1, capturer_id, epoch, software_version,
//     opened_utc, segments:[...], previous_epochs:[...]} is rewritten
//     (tmp + rename) on every segment close.
//   - A restart never appends to an existing file: new epoch, new segments.
//
// Concurrency: each source has one SourceWriter with a bounded channel and a
// dedicated goroutine; transports never touch files. A full channel is never
// silent: Offer() counts the drop and the writer goroutine emits
// GAP{reason:"drop"} with the count as soon as it can write again.
package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"hash/crc32"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

const schemaVersion = 1

// procStart anchors the monotonic clock. t_recv_mono_ns values are only ever
// compared within one process epoch (same contract as Python's monotonic_ns).
var procStart = time.Now()

// monoNs returns process-monotonic nanoseconds (time.Since uses the monotonic reading).
func monoNs() int64 { return int64(time.Since(procStart)) }

// isoUTC formats t like Python's datetime.isoformat() for an aware UTC time
// with microseconds: 2026-09-06T01:06:50.988888+00:00.
func isoUTC(t time.Time) string {
	return t.UTC().Format("2006-01-02T15:04:05.000000") + "+00:00"
}

// hourKey is the per-segment hour bucket: YYYYMMDDTHH in UTC.
func hourKey(t time.Time) string { return t.UTC().Format("20060102T15") }

func sha256Hex(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func sha256File(path string) (string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	h := sha256.New()
	n, err := io.Copy(h, f)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(h.Sum(nil)), n, nil
}

func newEpoch() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}

// ---------------------------------------------------------------- ordered JSON

// KV is one key/value pair of an ordered JSON object.
type KV struct {
	K string
	V any
}

// Obj is a JSON object that marshals its keys in insertion order (Go maps
// sort keys; Python dicts keep insertion order — parsers do not care, but the
// files read the same as the Python store's).
type Obj []KV

// Set replaces the value of an existing key or appends a new pair.
func (o *Obj) Set(k string, v any) {
	for i := range *o {
		if (*o)[i].K == k {
			(*o)[i].V = v
			return
		}
	}
	*o = append(*o, KV{k, v})
}

// Get returns the value of key k and whether it was present.
func (o Obj) Get(k string) (any, bool) {
	for _, kv := range o {
		if kv.K == k {
			return kv.V, true
		}
	}
	return nil, false
}

// MarshalJSON emits the object in insertion order without HTML escaping, so a
// UTF-8 body is stored as the same characters Python's ensure_ascii=False writes.
func (o Obj) MarshalJSON() ([]byte, error) {
	var buf bytes.Buffer
	buf.WriteByte('{')
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	for i, kv := range o {
		if i > 0 {
			buf.WriteByte(',')
		}
		if err := enc.Encode(kv.K); err != nil {
			return nil, err
		}
		buf.Truncate(buf.Len() - 1) // Encoder appends '\n'
		buf.WriteByte(':')
		if err := enc.Encode(kv.V); err != nil {
			return nil, err
		}
		buf.Truncate(buf.Len() - 1)
	}
	buf.WriteByte('}')
	return buf.Bytes(), nil
}

// marshalJSON encodes v without HTML escaping and without a trailing newline.
// (json.Marshal re-escapes <, > and & even in a Marshaler's output; an Encoder
// with SetEscapeHTML(false) is the only way to keep the characters literal.)
func marshalJSON(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	b := buf.Bytes()
	return b[:len(b)-1], nil
}

// encodeBody is the lossless body encoding: valid UTF-8 as a JSON string, else base64.
func encodeBody(body []byte) (string, string) {
	if utf8.Valid(body) {
		return string(body), "utf8"
	}
	return base64.StdEncoding.EncodeToString(body), "b64"
}

// decodeBody inverts encodeBody for a parsed record.
func decodeBody(rec map[string]any) ([]byte, error) {
	body, _ := rec["body"].(string)
	switch rec["body_encoding"] {
	case "utf8":
		return []byte(body), nil
	case "b64":
		return base64.StdEncoding.DecodeString(body)
	}
	return nil, errors.New("record has no body")
}

// bodyFields returns the DATA/GAP body fields: len, crc32, body_sha256, body, body_encoding.
func bodyFields(body []byte) Obj {
	enc, kind := encodeBody(body)
	return Obj{
		{"len", len(body)},
		{"crc32", crc32.ChecksumIEEE(body)},
		{"body_sha256", sha256Hex(body)},
		{"body", enc},
		{"body_encoding", kind},
	}
}

// ---------------------------------------------------------------- records

// Record is one logical record handed to a SourceWriter. Fields is the
// kind-specific payload (without kind/source/capturer_id/epoch/seq, which the
// writer stamps). TRecv is the receipt wall time (UTC) captured by the
// transport at the moment the bytes arrived; Mono is the matching monotonic reading.
type Record struct {
	Kind   string
	Fields Obj
	TRecv  time.Time
	Mono   int64
	Sync   bool // fdatasync after writing (GAP records)
}

// NewRecord stamps the two receipt clocks now.
func NewRecord(kind string, fields Obj) Record {
	return Record{Kind: kind, Fields: fields, TRecv: time.Now().UTC(), Mono: monoNs()}
}

// DataRecord builds a DATA record for a body received with the given envelope
// (envelopeKey is "http" or "transport"). key and srcSeq may be nil.
func DataRecord(key any, srcSeq any, envelopeKey string, envelope Obj, body []byte) Record {
	f := Obj{{"key", key}, {"src_seq", srcSeq}, {envelopeKey, envelope}}
	f = append(f, bodyFields(body)...)
	return NewRecord("DATA", f)
}

// truncateRunes cuts s to at most n bytes without splitting a UTF-8 sequence
// (a split sequence would be re-encoded as U+FFFD, changing the text).
func truncateRunes(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}

// GapRecord builds a GAP record. detail is truncated to 4000 bytes like Python
// (Python cuts characters; the cut here never lands inside a character).
func GapRecord(reason, detail string, key any, envelopeKey string, envelope Obj, body []byte) Record {
	detail = truncateRunes(detail, 4000)
	f := Obj{{"reason", reason}, {"detail", detail}, {"key", key}}
	if envelope != nil {
		f = append(f, KV{envelopeKey, envelope})
	}
	if body != nil {
		f = append(f, bodyFields(body)...)
	}
	r := NewRecord("GAP", f)
	r.Sync = true
	return r
}

// ---------------------------------------------------------------- store

// ManifestSegment is the typed view of one closed segment entry of
// MANIFEST.json. The gz_* fields are written by the Python store's
// compress_and_verify (the .jsonl is replaced by a verified .jsonl.gz); the
// Go daemon never writes them but must read and preserve them.
type ManifestSegment struct {
	Source    string `json:"source"`
	Path      string `json:"path"`
	Records   int    `json:"records"`
	FirstSeq  *int64 `json:"first_seq"`
	LastSeq   *int64 `json:"last_seq"`
	SHA256    string `json:"sha256"`
	Bytes     int64  `json:"bytes"`
	Epoch     string `json:"epoch"`
	ClosedUTC string `json:"closed_utc"`
	GzPath    string `json:"gz_path,omitempty"`
	GzSHA256  string `json:"gz_sha256,omitempty"`
	GzBytes   int64  `json:"gz_bytes,omitempty"`
}

// DataPath is the file that holds the segment's bytes: the gzip when the
// Python post-run compression replaced the plain file (raw_store.verify_store
// and seeing.replay use the same rule: gz_path or path).
func (s ManifestSegment) DataPath(root string) string {
	p := s.Path
	if s.GzPath != "" {
		p = s.GzPath
	}
	return filepath.Join(root, filepath.FromSlash(p))
}

// Manifest is MANIFEST.json. Segment entries are kept as the JSON that was
// read or written, so a manifest that passed through the Python store (which
// may add fields) is rewritten with every field intact.
type Manifest struct {
	SchemaVersion   int               `json:"schema_version"`
	CapturerID      string            `json:"capturer_id"`
	Epoch           string            `json:"epoch"`
	SoftwareVersion string            `json:"software_version"`
	OpenedUTC       string            `json:"opened_utc"`
	Segments        []json.RawMessage `json:"segments"`
	PreviousEpochs  []map[string]any  `json:"previous_epochs,omitempty"`
	ClosedUTC       string            `json:"closed_utc,omitempty"`
}

// Entries decodes the segment list into its typed view (unknown fields are
// still present in Segments and survive a rewrite).
func (m Manifest) Entries() ([]ManifestSegment, error) {
	out := make([]ManifestSegment, 0, len(m.Segments))
	for i, raw := range m.Segments {
		var s ManifestSegment
		if err := json.Unmarshal(raw, &s); err != nil {
			return out, fmt.Errorf("manifest segment %d: %w", i, err)
		}
		out = append(out, s)
	}
	return out, nil
}

// ReadManifest loads <root>/MANIFEST.json.
func ReadManifest(root string) (Manifest, error) {
	var man Manifest
	raw, err := os.ReadFile(filepath.Join(root, "MANIFEST.json"))
	if err != nil {
		return man, err
	}
	if err := json.Unmarshal(raw, &man); err != nil {
		return man, fmt.Errorf("MANIFEST.json: %w", err)
	}
	return man, nil
}

// Store owns the output directory, the manifest and one SourceWriter per source.
type Store struct {
	Root            string
	CapturerID      string
	Epoch           string
	SoftwareVersion string
	QueueSize       int

	mu       sync.Mutex
	manifest Manifest
	prevSHA  map[string]*string
	writers  map[string]*SourceWriter
	closed   bool
}

// OpenStore prepares <root>/segments, mints an epoch and continues the hash
// chain from an existing MANIFEST.json (previous run) if there is one.
func OpenStore(root, capturerID, softwareVersion string, queueSize int) (*Store, error) {
	if err := os.MkdirAll(filepath.Join(root, "segments"), 0o755); err != nil {
		return nil, err
	}
	if queueSize <= 0 {
		queueSize = 4096
	}
	s := &Store{Root: root, CapturerID: capturerID, Epoch: newEpoch(), SoftwareVersion: softwareVersion,
		QueueSize: queueSize, prevSHA: map[string]*string{}, writers: map[string]*SourceWriter{}}
	s.manifest = Manifest{SchemaVersion: schemaVersion, CapturerID: capturerID, Epoch: s.Epoch,
		SoftwareVersion: softwareVersion, OpenedUTC: isoUTC(time.Now()), Segments: []json.RawMessage{}}
	if old, err := ReadManifest(root); err == nil { // a corrupt manifest must not stop capture
		if entries, err := old.Entries(); err == nil {
			for _, seg := range entries {
				sha := seg.SHA256
				s.prevSHA[seg.Source] = &sha
			}
			s.manifest.Segments = old.Segments // verbatim: Python-added fields (gz_path, ...) are kept
			s.manifest.PreviousEpochs = append(old.PreviousEpochs,
				map[string]any{"epoch": old.Epoch, "opened_utc": old.OpenedUTC})
		}
	}
	return s, nil
}

// Writer returns (creating on first use) the dedicated writer of a source.
func (s *Store) Writer(source string) *SourceWriter {
	s.mu.Lock()
	defer s.mu.Unlock()
	if w, ok := s.writers[source]; ok {
		return w
	}
	w := newSourceWriter(s, source, s.QueueSize)
	s.writers[source] = w
	return w
}

// Writers returns all writers sorted by source name.
func (s *Store) Writers() []*SourceWriter {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]*SourceWriter, 0, len(s.writers))
	for _, w := range s.writers {
		out = append(out, w)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Source < out[j].Source })
	return out
}

// SyncAll requests an fdatasync on every writer (heartbeat rule).
func (s *Store) SyncAll() {
	for _, w := range s.Writers() {
		w.RequestSync()
	}
}

// Close drains and closes every writer (heartbeat writer last so its final
// record can describe the others), then stamps closed_utc into the manifest.
func (s *Store) Close() error {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return nil
	}
	s.closed = true
	s.mu.Unlock()
	var first error
	ws := s.Writers()
	var hb *SourceWriter
	for _, w := range ws {
		if w.Source == "heartbeat" {
			hb = w
			continue
		}
		if err := w.Close(); err != nil && first == nil {
			first = err
		}
	}
	if hb != nil {
		if err := hb.Close(); err != nil && first == nil {
			first = err
		}
	}
	s.mu.Lock()
	s.manifest.ClosedUTC = isoUTC(time.Now())
	s.mu.Unlock()
	if err := s.writeManifest(); err != nil && first == nil {
		first = err
	}
	return first
}

func (s *Store) writeManifest() error {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	s.mu.Lock()
	err := enc.Encode(s.manifest)
	s.mu.Unlock()
	if err != nil {
		return err
	}
	raw := buf.Bytes()
	path := filepath.Join(s.Root, "MANIFEST.json")
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err = f.Write(raw); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// ---------------------------------------------------------------- per-source writer

type segment struct {
	path     string
	f        *os.File
	w        *bufio.Writer
	hasher   hash.Hash
	hourKey  string
	records  int
	firstSeq *int64
	lastSeq  *int64
}

// SourceWriter is the single goroutine that appends to a source's segments.
type SourceWriter struct {
	Source string
	store  *Store

	ch      chan Record
	syncCh  chan struct{}
	done    chan struct{}
	closeMu sync.Once
	err     error

	// counters (atomic; read by the heartbeat)
	Enqueued     atomic.Int64
	Written      atomic.Int64
	Dropped      atomic.Int64
	pendingDrops atomic.Int64
	DataBytes    atomic.Int64
	LastDataMono atomic.Int64 // 0 = never
	LastDataUTC  atomic.Value // string

	seq int64
	seg *segment
}

func newSourceWriter(s *Store, source string, queue int) *SourceWriter {
	w := &SourceWriter{Source: source, store: s, ch: make(chan Record, queue),
		syncCh: make(chan struct{}, 1), done: make(chan struct{})}
	go w.run()
	return w
}

// Put enqueues a record, blocking while the queue is full (back-pressure;
// used by polling/tailing transports where the upstream can wait).
func (w *SourceWriter) Put(r Record) {
	w.Enqueued.Add(1)
	w.ch <- r
}

// Offer enqueues without blocking. A full queue drops the record, counts it
// and schedules GAP{reason:"drop"} — the drop is never silent.
func (w *SourceWriter) Offer(r Record) bool {
	select {
	case w.ch <- r:
		w.Enqueued.Add(1)
		return true
	default:
		w.Dropped.Add(1)
		w.pendingDrops.Add(1)
		return false
	}
}

// RequestSync asks the goroutine to fdatasync the open segment.
func (w *SourceWriter) RequestSync() {
	select {
	case w.syncCh <- struct{}{}:
	default:
	}
}

// Close drains the queue, writes the TRAILER, fdatasyncs and updates the manifest.
func (w *SourceWriter) Close() error {
	w.closeMu.Do(func() { close(w.ch) })
	<-w.done
	return w.err
}

// QueueLen is the current backlog.
func (w *SourceWriter) QueueLen() int { return len(w.ch) }

func (w *SourceWriter) run() {
	defer close(w.done)
	for {
		select {
		case r, ok := <-w.ch:
			if !ok {
				w.flushDrops()
				if err := w.closeSegment(); err != nil && w.err == nil {
					w.err = err
				}
				return
			}
			if err := w.write(r); err != nil && w.err == nil {
				w.err = err
				fmt.Fprintf(os.Stderr, "ingest: %s: write failed: %v\n", w.Source, err)
			}
			w.flushDrops()
		case <-w.syncCh:
			w.flushDrops()
			w.sync()
		}
	}
}

// flushDrops turns counted drops into a GAP{reason:"drop"} record.
func (w *SourceWriter) flushDrops() {
	n := w.pendingDrops.Swap(0)
	if n == 0 {
		return
	}
	g := GapRecord("drop", fmt.Sprintf("%d frame(s) dropped: writer queue full (capacity %d)", n, cap(w.ch)),
		nil, "", nil, nil)
	g.Fields = append(g.Fields, KV{"dropped", n})
	if err := w.write(g); err != nil && w.err == nil {
		w.err = err
	}
}

func (w *SourceWriter) sync() {
	if w.seg != nil {
		w.seg.w.Flush()
		fdatasync(w.seg.f)
	}
}

func (w *SourceWriter) openSegment(hk string) error {
	seq0 := w.seq
	name := fmt.Sprintf("%s__%s__%s__%s__%08d.jsonl", w.Source, w.store.CapturerID, w.store.Epoch[:8], hk, seq0)
	path := filepath.Join(w.store.Root, "segments", name)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	w.seg = &segment{path: path, f: f, w: bufio.NewWriterSize(f, 1<<16), hasher: sha256.New(), hourKey: hk}
	w.store.mu.Lock()
	prev := w.store.prevSHA[w.Source]
	w.store.mu.Unlock()
	var prevV any
	if prev != nil {
		prevV = *prev
	}
	meta := NewRecord("META", Obj{
		{"schema_version", schemaVersion},
		{"software_version", w.store.SoftwareVersion},
		{"prev_segment_sha256", prevV},
		{"segment_hour_utc", hk},
	})
	return w.appendLine(meta)
}

// appendLine stamps the common fields and writes one JSON line. Key order for
// META/TRAILER matches Python (kind, payload..., source, capturer_id, epoch, seq,
// t_recv_utc, t_recv_mono_ns); DATA/GAP/HEARTBEAT records carry the two clocks
// before the identity fields, again as Python's write() produces them.
func (w *SourceWriter) appendLine(r Record) error {
	seq := w.seq
	if r.TRecv.IsZero() {
		r.TRecv = time.Now().UTC()
		r.Mono = monoNs()
	}
	o := Obj{{"kind", r.Kind}}
	o = append(o, r.Fields...)
	if r.Kind == "META" || r.Kind == "TRAILER" {
		o = append(o, KV{"source", w.Source}, KV{"capturer_id", w.store.CapturerID}, KV{"epoch", w.store.Epoch}, KV{"seq", seq},
			KV{"t_recv_utc", isoUTC(r.TRecv)}, KV{"t_recv_mono_ns", r.Mono})
	} else {
		o = append(o, KV{"t_recv_utc", isoUTC(r.TRecv)}, KV{"t_recv_mono_ns", r.Mono},
			KV{"source", w.Source}, KV{"capturer_id", w.store.CapturerID}, KV{"epoch", w.store.Epoch}, KV{"seq", seq})
	}
	line, err := marshalJSON(o)
	if err != nil {
		return err
	}
	line = append(line, '\n')
	if _, err := w.seg.w.Write(line); err != nil {
		return err
	}
	w.seg.hasher.Write(line)
	w.seg.records++
	if w.seg.firstSeq == nil {
		v := seq
		w.seg.firstSeq = &v
	}
	v := seq
	w.seg.lastSeq = &v
	w.seq = seq + 1
	return nil
}

func (w *SourceWriter) write(r Record) error {
	if r.TRecv.IsZero() { // a record built without NewRecord: stamp now, never bucket into year 0001
		r.TRecv = time.Now().UTC()
		r.Mono = monoNs()
	}
	hk := hourKey(r.TRecv)
	if w.seg != nil && w.seg.hourKey != hk {
		if err := w.closeSegment(); err != nil {
			return err
		}
	}
	if w.seg == nil {
		if err := w.openSegment(hk); err != nil {
			return err
		}
	}
	if err := w.appendLine(r); err != nil {
		return err
	}
	w.Written.Add(1)
	if r.Kind == "DATA" {
		if n, ok := r.Fields.Get("len"); ok {
			if ln, ok := n.(int); ok {
				w.DataBytes.Add(int64(ln))
			}
		}
		w.LastDataMono.Store(r.Mono)
		w.LastDataUTC.Store(isoUTC(r.TRecv))
	}
	if r.Sync {
		w.sync()
	}
	return nil
}

// closeSegment writes the TRAILER (records/first_seq/last_seq of the lines
// before it; sha256 of all bytes before the trailer line), fdatasyncs, hashes
// the whole file, advances the hash chain and rewrites the manifest.
//
// Manifest convention (Python RawStore._close_segment): the manifest entry is
// built after the trailer line was appended, so its records count includes
// the trailer line and its last_seq is the trailer's seq; the TRAILER record
// itself excludes the trailer. verify_segment compares against the TRAILER.
func (w *SourceWriter) closeSegment() error {
	seg := w.seg
	if seg == nil {
		return nil
	}
	var first, last any
	if seg.firstSeq != nil {
		first = *seg.firstSeq
	}
	if seg.lastSeq != nil {
		last = *seg.lastSeq
	}
	trailer := NewRecord("TRAILER", Obj{
		{"records", seg.records}, {"first_seq", first}, {"last_seq", last},
		{"sha256_before_trailer", hex.EncodeToString(seg.hasher.Sum(nil))},
	})
	err := w.appendLine(trailer)
	w.seg = nil
	if err != nil {
		return err
	}
	// post-trailer values, as Python records them in the manifest
	records := seg.records
	firstSeq, lastSeq := seg.firstSeq, seg.lastSeq
	if err := seg.w.Flush(); err != nil {
		return err
	}
	fdatasync(seg.f)
	if err := seg.f.Close(); err != nil {
		return err
	}
	digest, size, err := sha256File(seg.path)
	if err != nil {
		return err
	}
	rel, _ := filepath.Rel(w.store.Root, seg.path)
	entry, err := marshalJSON(ManifestSegment{
		Source: w.Source, Path: filepath.ToSlash(rel), Records: records, FirstSeq: firstSeq, LastSeq: lastSeq,
		SHA256: digest, Bytes: size, Epoch: w.store.Epoch, ClosedUTC: isoUTC(time.Now()),
	})
	if err != nil {
		return err
	}
	w.store.mu.Lock()
	d := digest
	w.store.prevSHA[w.Source] = &d
	w.store.manifest.Segments = append(w.store.manifest.Segments, json.RawMessage(entry))
	w.store.mu.Unlock()
	return w.store.writeManifest()
}

// openSegmentFile opens a plain or gzip-compressed segment for reading.
func openSegmentFile(path string) (io.ReadCloser, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	if !strings.HasSuffix(path, ".gz") {
		return f, nil
	}
	gz, err := gzip.NewReader(f)
	if err != nil {
		f.Close()
		return nil, err
	}
	return struct {
		io.Reader
		io.Closer
	}{gz, f}, nil
}

// ---------------------------------------------------------------- verification (Go port of raw_store.verify_*)

// SegmentReport mirrors raw_store.verify_segment.
type SegmentReport struct {
	Path           string
	Records        int
	BadRecords     int
	HasTrailer     bool
	TrailerMatches bool
	SeqContiguous  bool
	ChainOK        bool
	OK             bool
}

// ReadSegment parses a segment (.jsonl or .jsonl.gz) into records (a tiny JSONL reader).
func ReadSegment(path string) ([]map[string]any, error) {
	f, err := openSegmentFile(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []map[string]any
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<30)
	for sc.Scan() {
		var m map[string]any
		if err := json.Unmarshal(sc.Bytes(), &m); err != nil {
			return out, fmt.Errorf("%s: unparseable line: %w", path, err)
		}
		out = append(out, m)
	}
	return out, sc.Err()
}

// VerifySegment re-hashes a segment (.jsonl or .jsonl.gz) and checks its
// TRAILER, body CRC/sha256 and seq continuity.
func VerifySegment(path string) SegmentReport {
	rep := SegmentReport{Path: path, ChainOK: true}
	f, err := openSegmentFile(path)
	if err != nil {
		return rep
	}
	defer f.Close()
	h := sha256.New()
	var seqs []int64
	var trailer map[string]any
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<30)
	for sc.Scan() {
		line := sc.Bytes()
		var rec map[string]any
		if err := json.Unmarshal(line, &rec); err != nil {
			rep.BadRecords++
			continue
		}
		if rec["kind"] == "TRAILER" {
			trailer = rec
			break
		}
		h.Write(line)
		h.Write([]byte{'\n'})
		rep.Records++
		if s, ok := rec["seq"].(float64); ok {
			seqs = append(seqs, int64(s))
		}
		if (rec["kind"] == "DATA" || rec["kind"] == "GAP") && rec["body"] != nil {
			body, err := decodeBody(rec)
			if err != nil {
				rep.BadRecords++
				continue
			}
			if c, ok := rec["crc32"].(float64); ok && uint32(c) != crc32.ChecksumIEEE(body) {
				rep.BadRecords++
			}
			if s, ok := rec["body_sha256"].(string); ok && s != "" && s != sha256Hex(body) {
				rep.BadRecords++
			}
		}
	}
	if err := sc.Err(); err != nil { // unreadable tail (I/O error, corrupt gzip): the segment is not intact
		rep.BadRecords++
	}
	rep.SeqContiguous = true
	for i := 1; i < len(seqs); i++ {
		if seqs[i] != seqs[i-1]+1 {
			rep.SeqContiguous = false
		}
	}
	rep.HasTrailer = trailer != nil
	if trailer != nil {
		rep.TrailerMatches = trailer["sha256_before_trailer"] == hex.EncodeToString(h.Sum(nil))
		n, _ := trailer["records"].(float64)
		rep.OK = rep.TrailerMatches && int(n) == rep.Records && rep.BadRecords == 0 && rep.SeqContiguous
	}
	return rep
}

// VerifyStore verifies every manifest segment (gz_path when the Python
// post-run compression replaced the plain file) and the per-source hash chain.
func VerifyStore(root string) (reports []SegmentReport, allOK bool, err error) {
	man, err := ReadManifest(root)
	if err != nil {
		return nil, false, err
	}
	entries, err := man.Entries()
	if err != nil {
		return nil, false, err
	}
	allOK = true
	prev := map[string]string{}
	for _, s := range entries {
		path := s.DataPath(root)
		r := VerifySegment(path)
		recs, rerr := ReadSegment(path)
		if p, seen := prev[s.Source]; seen {
			// an unreadable or empty first record breaks the chain, as in Python (which raises)
			r.ChainOK = false
			if rerr == nil && len(recs) > 0 {
				got, _ := recs[0]["prev_segment_sha256"].(string)
				r.ChainOK = got == p
			}
		}
		prev[s.Source] = s.SHA256
		if !r.OK || !r.ChainOK {
			allOK = false
		}
		reports = append(reports, r)
	}
	return reports, allOK, nil
}

// describe renders a report for the -verify command.
func (r SegmentReport) String() string {
	return fmt.Sprintf("%-6v %s records=%d bad=%d trailer=%v chain=%v seq=%v",
		map[bool]string{true: "ok", false: "FAIL"}[r.OK && r.ChainOK], filepath.Base(r.Path),
		r.Records, r.BadRecords, r.TrailerMatches, r.ChainOK, r.SeqContiguous)
}
