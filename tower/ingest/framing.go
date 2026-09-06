// Stream framing for byte streams (tcp, file_tail).
//
// A Framer cuts a byte stream into frames. Frames are returned wire-exact —
// every byte of the stream that belongs to a frame is inside that frame
// (delimiters, length prefixes and FIX trailers included), so the raw store
// keeps what was on the wire, and a reader can re-concatenate frames to
// obtain the original stream. Bytes that belong to no frame (garbage before a
// FIX BeginString, a corrupt header) are counted in Skipped() so the
// transport can record them as GAP{reason:"resync"} — never silently dropped.
//
// Framings:
//
//	line   frames end at a delimiter (default "\n"); the delimiter is kept.
//	soh    FIX: "8=<BeginString><SOH>9=<BodyLength><SOH>" + BodyLength bytes +
//	       "10=xxx<SOH>" (7 bytes). BodyLength is counted from the byte after
//	       the SOH that terminates tag 9 up to and including the SOH before
//	       "10=". If the checksum tag is not where BodyLength says, the framer
//	       falls back to scanning for "<SOH>10=xxx<SOH>" so a wrong length
//	       still yields the whole message (and the record keeps the bytes).
//	len16  2-byte big-endian length prefix + payload (ITCH/SoupBinTCP style);
//	       the prefix is kept in the frame.
package main

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"strconv"
)

// Framer yields wire-exact frames from a stream.
type Framer interface {
	// Next returns the next complete frame. io.EOF (or io.ErrUnexpectedEOF
	// when a frame is cut) ends the stream; the partial tail is available via Partial().
	Next() ([]byte, error)
	// Skipped returns the number of bytes discarded since the last call (resync).
	Skipped() int64
	// Partial returns bytes read but not yet framed (a cut frame at EOF).
	Partial() []byte
}

// NewFramer builds a framer of the named kind. delimiter applies to "line".
func NewFramer(kind string, r io.Reader, delimiter string) (Framer, error) {
	br := bufio.NewReaderSize(r, 1<<16)
	switch kind {
	case "line", "":
		if delimiter == "" {
			delimiter = "\n"
		}
		return &lineFramer{r: br, delim: []byte(delimiter)}, nil
	case "soh":
		return &sohFramer{r: br}, nil
	case "len16":
		return &len16Framer{r: br}, nil
	}
	return nil, fmt.Errorf("unknown framing %q (line|soh|len16)", kind)
}

// ---------------------------------------------------------------- line

type lineFramer struct {
	r       *bufio.Reader
	delim   []byte
	buf     []byte
	pending error // a read error that arrived together with data: surfaced once the data is framed
}

// takePending returns (and clears) a deferred read error, mapping EOF inside
// a frame to io.ErrUnexpectedEOF. Errors are never sticky: a later Next
// reads again, so a transient error (a tailed file re-opened, a socket
// deadline) does not turn the framer into a permanent EOF.
func takePending(pending *error, partial int) error {
	err := *pending
	*pending = nil
	if errors.Is(err, io.EOF) && partial > 0 {
		return io.ErrUnexpectedEOF
	}
	return err
}

func (l *lineFramer) Next() ([]byte, error) {
	for {
		if i := bytes.Index(l.buf, l.delim); i >= 0 {
			end := i + len(l.delim)
			frame := append([]byte(nil), l.buf[:end]...)
			l.buf = l.buf[end:]
			return frame, nil
		}
		if l.pending != nil {
			return nil, takePending(&l.pending, len(l.buf))
		}
		chunk := make([]byte, 1<<14)
		n, err := l.r.Read(chunk)
		l.buf = append(l.buf, chunk[:n]...)
		if err != nil {
			l.pending = err // the bytes that came with it are framed first
		}
	}
}

func (l *lineFramer) Skipped() int64  { return 0 }
func (l *lineFramer) Partial() []byte { return l.buf }

// ---------------------------------------------------------------- soh (FIX)

const soh = byte(0x01)

type sohFramer struct {
	r       *bufio.Reader
	buf     []byte
	skipped int64
	pending error
}

// fill reads one chunk. An error that arrives with data is deferred until the
// data has been framed; errors are not sticky (see takePending).
func (s *sohFramer) fill() error {
	if s.pending != nil {
		return takePending(&s.pending, len(s.buf))
	}
	chunk := make([]byte, 1<<14)
	n, err := s.r.Read(chunk)
	s.buf = append(s.buf, chunk[:n]...)
	if err != nil {
		if n > 0 {
			s.pending = err
			return nil
		}
		if errors.Is(err, io.EOF) && len(s.buf) > 0 {
			return io.ErrUnexpectedEOF
		}
		return err
	}
	return nil
}

// parseHeader finds "8=...<SOH>9=<n><SOH>" at the start of buf. Returns the
// header length and BodyLength, or need=true when more bytes are required,
// or bad=true when the bytes at the start cannot be a FIX header.
func parseFIXHeader(buf []byte) (hdrLen int, bodyLen int, need bool, bad bool) {
	if len(buf) < 2 {
		return 0, 0, true, false
	}
	if !bytes.HasPrefix(buf, []byte("8=")) {
		return 0, 0, false, true
	}
	i := bytes.IndexByte(buf, soh)
	if i < 0 {
		return 0, 0, len(buf) < 64, len(buf) >= 64
	}
	rest := buf[i+1:]
	if len(rest) < 2 {
		return 0, 0, true, false
	}
	if !bytes.HasPrefix(rest, []byte("9=")) {
		return 0, 0, false, true
	}
	j := bytes.IndexByte(rest, soh)
	if j < 0 {
		return 0, 0, len(rest) < 32, len(rest) >= 32
	}
	n, err := strconv.Atoi(string(rest[2:j]))
	if err != nil || n < 0 {
		return 0, 0, false, true
	}
	return i + 1 + j + 1, n, false, false
}

func (s *sohFramer) Next() ([]byte, error) {
	for {
		// resync: drop bytes until "8=" is at the front
		if k := bytes.Index(s.buf, []byte("8=")); k > 0 {
			s.skipped += int64(k)
			s.buf = s.buf[k:]
		} else if k < 0 && len(s.buf) > 1 {
			// keep a trailing "8" in case the "=" is in the next chunk
			keep := 0
			if s.buf[len(s.buf)-1] == '8' {
				keep = 1
			}
			s.skipped += int64(len(s.buf) - keep)
			s.buf = s.buf[len(s.buf)-keep:]
		}
		hdr, body, need, bad := parseFIXHeader(s.buf)
		if bad {
			s.skipped++
			s.buf = s.buf[1:]
			continue
		}
		if !need {
			total := hdr + body + 7 // "10=xxx<SOH>"
			if len(s.buf) >= total {
				if bytes.HasPrefix(s.buf[hdr+body:], []byte("10=")) && s.buf[total-1] == soh {
					frame := append([]byte(nil), s.buf[:total]...)
					s.buf = s.buf[total:]
					return frame, nil
				}
				// BodyLength lies: scan for the checksum trailer instead
				if m := bytes.Index(s.buf[hdr:], []byte{soh, '1', '0', '='}); m >= 0 {
					end := bytes.IndexByte(s.buf[hdr+m+1:], soh)
					if end >= 0 {
						total = hdr + m + 1 + end + 1
						frame := append([]byte(nil), s.buf[:total]...)
						s.buf = s.buf[total:]
						return frame, nil
					}
				} else {
					s.skipped++
					s.buf = s.buf[1:]
					continue
				}
			}
		}
		if err := s.fill(); err != nil {
			return nil, err // EOF inside a frame is already io.ErrUnexpectedEOF; other errors keep their identity
		}
	}
}

func (s *sohFramer) Skipped() int64 {
	n := s.skipped
	s.skipped = 0
	return n
}
func (s *sohFramer) Partial() []byte { return s.buf }

// ---------------------------------------------------------------- len16

type len16Framer struct {
	r       *bufio.Reader
	buf     []byte
	pending error
}

func (l *len16Framer) Next() ([]byte, error) {
	for {
		if len(l.buf) >= 2 {
			n := int(binary.BigEndian.Uint16(l.buf[:2]))
			if len(l.buf) >= 2+n {
				frame := append([]byte(nil), l.buf[:2+n]...)
				l.buf = l.buf[2+n:]
				return frame, nil
			}
		}
		if l.pending != nil {
			return nil, takePending(&l.pending, len(l.buf))
		}
		chunk := make([]byte, 1<<14)
		n, err := l.r.Read(chunk)
		l.buf = append(l.buf, chunk[:n]...)
		if err != nil {
			l.pending = err
		}
	}
}

func (l *len16Framer) Skipped() int64  { return 0 }
func (l *len16Framer) Partial() []byte { return l.buf }

// fixTag returns the value of a FIX tag in a wire-exact frame, "" if absent.
func fixTag(frame []byte, tag string) string {
	needle := []byte(tag + "=")
	for _, part := range bytes.Split(frame, []byte{soh}) {
		if bytes.HasPrefix(part, needle) {
			return string(part[len(needle):])
		}
	}
	return ""
}
