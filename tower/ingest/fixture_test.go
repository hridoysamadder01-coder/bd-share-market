package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// TestVerifyPythonWrittenFixtureStore is the reverse compatibility check: the
// Go verifier (same rules as raw_store.verify_store) must accept a real
// closed-market capture written by the Python RawStore.
func TestVerifyPythonWrittenFixtureStore(t *testing.T) {
	root := filepath.Join("..", "..", "tests", "fixtures", "capture_closed")
	if _, err := os.Stat(filepath.Join(root, "MANIFEST.json")); err != nil {
		t.Skip("fixture capture not present")
	}
	// The committed fixture is trimmed: its manifest lists a few segments whose
	// files were left out (the Python verify_store raises on them too). Verify
	// every segment that is present, chain included, with the same rules.
	raw, err := os.ReadFile(filepath.Join(root, "MANIFEST.json"))
	if err != nil {
		t.Fatal(err)
	}
	var man Manifest
	if err := json.Unmarshal(raw, &man); err != nil {
		t.Fatal(err)
	}
	prev := map[string]string{}
	present := 0
	for _, s := range man.Segments {
		path := filepath.Join(root, filepath.FromSlash(s.Path))
		if _, err := os.Stat(path); err != nil {
			continue
		}
		present++
		r := VerifySegment(path)
		recs, rerr := ReadSegment(path)
		if rerr != nil {
			t.Fatal(rerr)
		}
		if p, seen := prev[s.Source]; seen {
			got, _ := recs[0]["prev_segment_sha256"].(string)
			r.ChainOK = got == p
		}
		prev[s.Source] = s.SHA256
		digest, size, _ := sha256File(path)
		// manifest records include the trailer line (Python convention); the verifier's count excludes it
		if !r.OK || !r.ChainOK || digest != s.SHA256 || size != s.Bytes || r.Records+1 != s.Records {
			t.Errorf("%s (manifest sha match=%v bytes match=%v)", r.String(), digest == s.SHA256, size == s.Bytes)
		}
	}
	if present < 5 {
		t.Fatalf("expected several present segments, got %d", present)
	}
	// bodies decode through the same utf8/b64 rule and re-hash to body_sha256
	recs, err := ReadSegment(filepath.Join(root, "segments", "dsebd_depth__ccr-vm__0021fcbc__20260906T01__00000000.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, r := range recs {
		if r["kind"] != "DATA" {
			continue
		}
		body, err := decodeBody(r)
		if err != nil || sha256Hex(body) != r["body_sha256"] || len(body) != int(r["len"].(float64)) {
			t.Fatalf("seq %v: body does not round-trip", r["seq"])
		}
		n++
	}
	if n == 0 {
		t.Fatal("no DATA records in fixture segment")
	}
}
