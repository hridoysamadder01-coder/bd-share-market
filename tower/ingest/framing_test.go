package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"testing"
)

// drip returns one byte per Read so frames always straddle reads.
type drip struct{ b []byte }

func (d *drip) Read(p []byte) (int, error) {
	if len(d.b) == 0 {
		return 0, io.EOF
	}
	p[0] = d.b[0]
	d.b = d.b[1:]
	return 1, nil
}

// fixMsg builds a wire-exact FIX message with a correct BodyLength and checksum.
func fixMsg(seq int, body string) []byte {
	b := fmt.Sprintf("35=W\x0134=%d\x01%s", seq, body)
	head := fmt.Sprintf("8=FIX.4.4\x019=%d\x01", len(b))
	msg := head + b
	sum := 0
	for i := 0; i < len(msg); i++ {
		sum += int(msg[i])
	}
	return []byte(fmt.Sprintf("%s10=%03d\x01", msg, sum%256))
}

func collect(t *testing.T, fr Framer) [][]byte {
	t.Helper()
	var out [][]byte
	for {
		f, err := fr.Next()
		if err != nil {
			if !errors.Is(err, io.EOF) {
				t.Fatalf("unexpected error %v", err)
			}
			return out
		}
		out = append(out, f)
	}
}

func TestLineFramingKeepsDelimiterAndStraddlesReads(t *testing.T) {
	stream := []byte("a\r\nbb\n\n{\"x\":1}\n")
	for _, r := range []io.Reader{bytes.NewReader(stream), &drip{stream}} {
		fr, _ := NewFramer("line", r, "")
		frames := collect(t, fr)
		want := [][]byte{[]byte("a\r\n"), []byte("bb\n"), []byte("\n"), []byte("{\"x\":1}\n")}
		if len(frames) != len(want) {
			t.Fatalf("got %q", frames)
		}
		for i := range want {
			if !bytes.Equal(frames[i], want[i]) {
				t.Fatalf("frame %d: %q", i, frames[i])
			}
		}
		if !bytes.Equal(bytes.Join(frames, nil), stream) {
			t.Fatal("frames must re-concatenate to the stream")
		}
	}
	// custom delimiter and a partial tail
	fr, _ := NewFramer("line", bytes.NewReader([]byte("x|y|zz")), "|")
	f1, _ := fr.Next()
	f2, _ := fr.Next()
	_, err := fr.Next()
	if string(f1) != "x|" || string(f2) != "y|" || !errors.Is(err, io.ErrUnexpectedEOF) || string(fr.Partial()) != "zz" {
		t.Fatalf("%q %q %v %q", f1, f2, err, fr.Partial())
	}
}

func TestSOHFramingFIX(t *testing.T) {
	m1 := fixMsg(1, "55=ABC\x01268=2\x01")
	m2 := fixMsg(2, "55=DEF\x01")
	stream := append(append([]byte{}, m1...), m2...)
	for _, r := range []io.Reader{bytes.NewReader(stream), &drip{stream}} {
		fr, _ := NewFramer("soh", r, "")
		frames := collect(t, fr)
		if len(frames) != 2 || !bytes.Equal(frames[0], m1) || !bytes.Equal(frames[1], m2) {
			t.Fatalf("got %q", frames)
		}
		if fixTag(frames[1], "34") != "2" || fixTag(frames[0], "55") != "ABC" {
			t.Fatal("fixTag")
		}
	}
	// garbage before the first message is skipped and counted, never merged into a frame
	stream = append([]byte("junk\x01garbage 8"), m1...)
	fr, _ := NewFramer("soh", bytes.NewReader(stream), "")
	f, err := fr.Next()
	if err != nil || !bytes.Equal(f, m1) {
		t.Fatalf("%q %v", f, err)
	}
	if fr.Skipped() != int64(len("junk\x01garbage 8")) {
		t.Fatalf("skipped count")
	}
	// wrong BodyLength: the framer falls back to the checksum trailer and still yields the whole message
	bad := bytes.Replace(m2, []byte("9=13\x01"), []byte("9=99\x01"), 1)
	if bytes.Equal(bad, m2) {
		bad = bytes.Replace(m2, []byte("9="+fmt.Sprint(len("35=W\x0134=2\x0155=DEF\x01"))+"\x01"), []byte("9=5\x01"), 1)
	}
	fr, _ = NewFramer("soh", bytes.NewReader(append(append([]byte{}, bad...), m1...)), "")
	frames := collect(t, fr)
	if len(frames) != 2 || !bytes.Equal(frames[0], bad) || !bytes.Equal(frames[1], m1) {
		t.Fatalf("bad-length fallback: %q", frames)
	}
	// cut message → ErrUnexpectedEOF with the partial bytes retained
	fr, _ = NewFramer("soh", bytes.NewReader(m1[:len(m1)-3]), "")
	if _, err := fr.Next(); !errors.Is(err, io.ErrUnexpectedEOF) || len(fr.Partial()) != len(m1)-3 {
		t.Fatalf("partial: %v %d", err, len(fr.Partial()))
	}
}

func len16(payload []byte) []byte {
	b := make([]byte, 2+len(payload))
	binary.BigEndian.PutUint16(b, uint16(len(payload)))
	copy(b[2:], payload)
	return b
}

func TestLen16Framing(t *testing.T) {
	f1 := len16([]byte("A\x00\x01\xff"))
	f2 := len16([]byte{})
	f3 := len16(bytes.Repeat([]byte("z"), 300))
	stream := bytes.Join([][]byte{f1, f2, f3}, nil)
	for _, r := range []io.Reader{bytes.NewReader(stream), &drip{stream}} {
		fr, _ := NewFramer("len16", r, "")
		frames := collect(t, fr)
		if len(frames) != 3 || !bytes.Equal(frames[0], f1) || !bytes.Equal(frames[1], f2) || !bytes.Equal(frames[2], f3) {
			t.Fatalf("got %d frames", len(frames))
		}
		if binary.BigEndian.Uint16(frames[2]) != 300 {
			t.Fatal("prefix kept")
		}
	}
	fr, _ := NewFramer("len16", bytes.NewReader(f3[:100]), "")
	if _, err := fr.Next(); !errors.Is(err, io.ErrUnexpectedEOF) || len(fr.Partial()) != 100 {
		t.Fatalf("partial %v", err)
	}
	if _, err := NewFramer("nope", bytes.NewReader(nil), ""); err == nil {
		t.Fatal("unknown framing must error")
	}
}
