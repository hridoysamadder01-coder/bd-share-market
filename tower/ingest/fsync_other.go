//go:build !linux

package main

import "os"

// fdatasync on platforms without fdatasync(2): a full fsync.
func fdatasync(f *os.File) { _ = f.Sync() }
