// ingest — zero-loss capture daemon for the DSE Observation Tower.
//
//	go build ./... && ./ingest -config cfg.json
//	./ingest -verify <out>          # re-hash every segment listed in MANIFEST.json
//
// Config JSON:
//
//	{
//	  "out": "evidence/capture/today",       // store root (segments/, MANIFEST.json, status.json, state/)
//	  "capturer_id": "ccr-vm",              // default: hostname
//	  "software_version": "tower-ingest/0.1",
//	  "heartbeat_ms": 5000,                 // HEARTBEAT cadence (+ fdatasync of every open segment)
//	  "queue_size": 4096,                   // bounded per-source writer queue
//	  "sources": [ {SourceConfig}, ... ]    // see transports.go
//	}
//
// The daemon writes a META record to source "runner" with the effective
// configuration, runs every transport in its own goroutine, heartbeats with
// per-source counters, and on SIGTERM/SIGINT stops the transports, drains the
// writer queues, writes every TRAILER and the final MANIFEST.json.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

const defaultSoftwareVersion = "tower-ingest/0.1"

// Config is the daemon configuration file.
type Config struct {
	Out             string         `json:"out"`
	CapturerID      string         `json:"capturer_id"`
	SoftwareVersion string         `json:"software_version"`
	HeartbeatMs     int            `json:"heartbeat_ms"`
	QueueSize       int            `json:"queue_size"`
	Sources         []SourceConfig `json:"sources"`
}

func loadConfig(path string) (Config, error) {
	var cfg Config
	raw, err := os.ReadFile(path)
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return cfg, fmt.Errorf("%s: %w", path, err)
	}
	if cfg.Out == "" {
		return cfg, errors.New("config: out is required")
	}
	if cfg.CapturerID == "" {
		h, err := os.Hostname()
		if err != nil || h == "" {
			h = "ingest"
		}
		cfg.CapturerID = h
	}
	if cfg.SoftwareVersion == "" {
		cfg.SoftwareVersion = defaultSoftwareVersion
	}
	if cfg.HeartbeatMs <= 0 {
		cfg.HeartbeatMs = 5000
	}
	if len(cfg.Sources) == 0 {
		return cfg, errors.New("config: at least one source is required")
	}
	seen := map[string]bool{}
	for _, s := range cfg.Sources {
		if seen[s.Name] {
			return cfg, fmt.Errorf("config: duplicate source name %q", s.Name)
		}
		seen[s.Name] = true
		if s.Name == "heartbeat" || s.Name == "runner" {
			return cfg, fmt.Errorf("config: source name %q is reserved", s.Name)
		}
	}
	return cfg, nil
}

// Daemon wires store, transports and heartbeat together.
type Daemon struct {
	cfg        Config
	store      *Store
	transports []Transport
	started    time.Time
}

// NewDaemon opens the store and builds every transport (no network yet).
func NewDaemon(cfg Config) (*Daemon, error) {
	store, err := OpenStore(cfg.Out, cfg.CapturerID, cfg.SoftwareVersion, cfg.QueueSize)
	if err != nil {
		return nil, err
	}
	d := &Daemon{cfg: cfg, store: store}
	for _, sc := range cfg.Sources {
		t, err := NewTransport(sc, store)
		if err != nil {
			return nil, err
		}
		d.transports = append(d.transports, t)
	}
	return d, nil
}

// status is the HEARTBEAT payload: per-source counters, ages since the last
// DATA record (null when a source has never delivered), writer queues.
func (d *Daemon) status() Obj {
	now := time.Now()
	ages := Obj{}
	counts := Obj{}
	states := Obj{}
	var dropped int64
	for _, t := range d.transports {
		w := d.store.Writer(t.Name())
		var age any
		if m := w.LastDataMono.Load(); m > 0 {
			age = float64((monoNs()-m)/1e6) / 1e3
		}
		ages = append(ages, KV{t.Name(), age})
		st := t.Status()
		counts = append(counts, KV{t.Name(), st})
		if s, ok := st.Get("state"); ok {
			states = append(states, KV{t.Name(), s})
		}
		dropped += w.Dropped.Load()
	}
	return Obj{
		{"t_utc", isoUTC(now)},
		{"uptime_s", float64(now.Sub(d.started)/time.Millisecond) / 1e3},
		{"ages_s", ages},
		{"counts", counts},
		{"states", states},
		{"dropped_total", dropped},
		{"sources", len(d.transports)},
	}
}

func (d *Daemon) writeStatusFile() {
	raw, err := json.MarshalIndent(Obj{{"capturer_id", d.cfg.CapturerID}, {"epoch", d.store.Epoch}, {"status", d.status()}}, "", " ")
	if err != nil {
		return
	}
	path := filepath.Join(d.cfg.Out, "status.json")
	if err := os.WriteFile(path+".tmp", raw, 0o644); err == nil {
		_ = os.Rename(path+".tmp", path)
	}
}

// heartbeat writes HEARTBEAT{status} to source "heartbeat" and fdatasyncs every open segment.
func (d *Daemon) heartbeat() {
	hb := d.store.Writer("heartbeat")
	hb.Put(NewRecord("HEARTBEAT", Obj{{"status", d.status()}}))
	d.store.SyncAll()
	d.writeStatusFile()
}

// Run blocks until ctx is cancelled, then shuts down cleanly.
func (d *Daemon) Run(ctx context.Context) error {
	d.started = time.Now()
	srcs := make([]any, 0, len(d.cfg.Sources))
	for _, s := range d.cfg.Sources {
		srcs = append(srcs, Obj{{"name", s.Name}, {"type", s.Type}, {"url", s.URL}, {"addr", s.Addr},
			{"path", s.Path}, {"framing", s.Framing}, {"interval_ms", s.IntervalMs}})
	}
	runner := d.store.Writer("runner")
	runner.Put(NewRecord("META", Obj{{"started", true}, {"out", d.cfg.Out}, {"heartbeat_ms", d.cfg.HeartbeatMs},
		{"queue_size", d.store.QueueSize}, {"sources", srcs}, {"note", "raw-first; parse on replay only"}}))
	runner.RequestSync()

	var wg sync.WaitGroup
	for _, t := range d.transports {
		wg.Add(1)
		go func(t Transport) {
			defer wg.Done()
			t.Run(ctx)
		}(t)
	}
	d.heartbeat()
	tick := time.NewTicker(time.Duration(d.cfg.HeartbeatMs) * time.Millisecond)
	defer tick.Stop()
loop:
	for {
		select {
		case <-ctx.Done():
			break loop
		case <-tick.C:
			d.heartbeat()
		}
	}
	wg.Wait() // transports have stopped: every record they will ever produce is enqueued
	runner.Put(NewRecord("META", Obj{{"finished", true}, {"stopped_by_signal", true}, {"status", d.status()}}))
	d.heartbeat()
	return d.store.Close()
}

func main() {
	cfgPath := flag.String("config", "", "config JSON path")
	verify := flag.String("verify", "", "verify a store directory and exit")
	runFor := flag.Duration("run-for", 0, "stop after this duration (0 = until SIGTERM/SIGINT)")
	flag.Parse()

	if *verify != "" {
		reports, ok, err := VerifyStore(*verify)
		if err != nil {
			fmt.Fprintln(os.Stderr, "verify:", err)
			os.Exit(2)
		}
		for _, r := range reports {
			fmt.Println(r.String())
		}
		fmt.Printf("segments=%d all_ok=%v\n", len(reports), ok)
		if !ok {
			os.Exit(1)
		}
		return
	}
	if *cfgPath == "" {
		fmt.Fprintln(os.Stderr, "usage: ingest -config cfg.json | ingest -verify DIR")
		os.Exit(2)
	}
	cfg, err := loadConfig(*cfgPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "config:", err)
		os.Exit(2)
	}
	d, err := NewDaemon(cfg)
	if err != nil {
		fmt.Fprintln(os.Stderr, "start:", err)
		os.Exit(2)
	}
	ctx, cancel := context.WithCancel(context.Background())
	if *runFor > 0 {
		ctx, cancel = context.WithTimeout(ctx, *runFor)
	}
	sig := make(chan os.Signal, 2)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		s := <-sig
		fmt.Fprintf(os.Stderr, "ingest: %v: closing segments\n", s)
		cancel()
	}()
	fmt.Fprintf(os.Stderr, "ingest: capturer=%s epoch=%s out=%s sources=%d\n", cfg.CapturerID, d.store.Epoch, cfg.Out, len(cfg.Sources))
	if err := d.Run(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "ingest: close:", err)
		os.Exit(1)
	}
	cancel()
}
