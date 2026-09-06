//go:build linux

package main

import (
	"os"
	"syscall"
)

// fdatasync flushes file data (and the metadata needed to read it back) to
// stable storage. Falls back to fsync where fdatasync is not implemented.
func fdatasync(f *os.File) {
	if err := syscall.Fdatasync(int(f.Fd())); err != nil {
		_ = f.Sync()
	}
}
