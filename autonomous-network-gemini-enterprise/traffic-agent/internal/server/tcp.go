// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package server provides TCP and UDP traffic receivers for the destination role.
// Unlike iperf3's single-client server, these servers accept an unlimited number
// of simultaneous connections — each handled in its own goroutine.
package server

import (
	"context"
	"io"
	"log/slog"
	"net"
	"fmt"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/metrics"
)

// TCPServer listens for incoming TCP connections and absorbs all data,
// recording bytes received in the provided metrics.Collector.
type TCPServer struct {
	port    int
	metrics *metrics.Collector
	logger  *slog.Logger
}

// NewTCP creates a TCPServer on the given port.
func NewTCP(port int, m *metrics.Collector, logger *slog.Logger) *TCPServer {
	if logger == nil {
		logger = slog.Default()
	}
	return &TCPServer{port: port, metrics: m, logger: logger}
}

// Serve starts listening and accepts connections until ctx is cancelled.
// Each accepted connection is handled in a new goroutine.
func (s *TCPServer) Serve(ctx context.Context) error {
	addr := fmt.Sprintf(":%d", s.port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("tcp listen on %s: %w", addr, err)
	}
	s.logger.Info("TCP server listening", "addr", addr)

	// Close the listener when ctx is done so Accept unblocks.
	go func() {
		<-ctx.Done()
		ln.Close()
	}()

	for {
		conn, err := ln.Accept()
		if err != nil {
			select {
			case <-ctx.Done():
				return nil // clean shutdown
			default:
				s.logger.Warn("accept error", "err", err)
				continue
			}
		}
		s.logger.Debug("accepted connection", "remote", conn.RemoteAddr())
		go s.handleConn(ctx, conn)
	}
}

func (s *TCPServer) handleConn(ctx context.Context, conn net.Conn) {
	defer conn.Close()
	s.metrics.IncrSessions()
	defer s.metrics.DecrSessions()

	remote := conn.RemoteAddr().String()
	var connBytes int64

	defer func() {
		s.logger.Debug("connection closed", "remote", remote, "bytes_received", connBytes)
	}()

	buf := make([]byte, 65536) // 64 KB read buffer
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		n, err := conn.Read(buf)
		if n > 0 {
			s.metrics.AddBytesReceived(int64(n))
			connBytes += int64(n)
		}
		if err != nil {
			if err != io.EOF {
				s.logger.Debug("conn read error", "remote", remote, "err", err, "bytes_received", connBytes)
			}
			return
		}
	}
}
