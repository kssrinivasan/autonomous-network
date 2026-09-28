// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// traffic-agent is the replacement for the Python traffic_generator.py + iperf3.
//
// It supports three modes:
//
//	daemon  — long-lived HTTP agent; operator calls /v1/flows to control flows.
//	          This is the Phase 2 mode started by the container entrypoint.
//
//	run     — one-shot client mode.  Reads a JSON config file (same format as
//	          the existing Ansible-generated config_PORT.json) and runs the
//	          traffic test, outputting results as JSON to stdout.
//	          Drop-in replacement for traffic_generator.py in Phase 1.
//
//	serve   — one-shot server mode.  Starts a TCP/UDP traffic receiver on the
//	          given port for the given duration.
//	          Drop-in replacement for the iperf3 server in Phase 1.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/api"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/bandwidth"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/config"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/flowmanager"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/metrics"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/patterns"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/server"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/session"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	// Configure structured logging.
	logLevel := new(slog.LevelVar)
	if os.Getenv("LOG_LEVEL") == "debug" {
		logLevel.Set(slog.LevelDebug)
	}
	logger := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: logLevel}))
	slog.SetDefault(logger)

	switch os.Args[1] {
	case "daemon":
		runDaemon(logger, os.Args[2:])
	case "run":
		runOneShot(logger, os.Args[2:])
	case "serve":
		runServe(logger, os.Args[2:])
	case "version":
		fmt.Println("traffic-agent v1.0.0")
	default:
		usage()
		os.Exit(1)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Daemon mode: long-lived HTTP agent
// ─────────────────────────────────────────────────────────────────────────────

func runDaemon(logger *slog.Logger, args []string) {
	fs := flag.NewFlagSet("daemon", flag.ExitOnError)
	controlPort := fs.Int("control-port", 9090, "HTTP control API port")
	metricsPort := fs.Int("metrics-port", 9091, "Prometheus metrics scrape port")
	_ = fs.Parse(args)

	mgr := flowmanager.New(logger)
	handler := api.New(mgr, logger)

	// Control API server (port 9090 by default).
	controlAddr := fmt.Sprintf(":%d", *controlPort)
	controlSrv := &http.Server{
		Addr:         controlAddr,
		Handler:      handler,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 30 * time.Second,
	}

	// Prometheus metrics server (port 9091 by default).
	// Serves GET /metrics in the Prometheus text exposition format.
	// The Ops Agent on the network VM scrapes this endpoint.
	metricsMux := http.NewServeMux()
	metricsMux.Handle("/metrics", metrics.PrometheusHandler(func() []metrics.FlowSnapshot {
		return mgr.FlowSnapshots()
	}))
	metricsAddr := fmt.Sprintf(":%d", *metricsPort)
	metricsSrv := &http.Server{
		Addr:         metricsAddr,
		Handler:      metricsMux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	// Graceful shutdown on SIGTERM / SIGINT.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go func() {
		logger.Info("traffic-agent daemon starting", "control_addr", controlAddr, "metrics_addr", metricsAddr)
		if err := controlSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("control API listen error", "err", err)
			os.Exit(1)
		}
	}()

	go func() {
		if err := metricsSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("metrics server listen error", "err", err)
			os.Exit(1)
		}
	}()

	<-ctx.Done()
	logger.Info("shutting down daemon")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := controlSrv.Shutdown(shutdownCtx); err != nil {
		logger.Warn("control server shutdown error", "err", err)
	}
	if err := metricsSrv.Shutdown(shutdownCtx); err != nil {
		logger.Warn("metrics server shutdown error", "err", err)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// One-shot source mode: reads JSON config, runs traffic, outputs results JSON
// ─────────────────────────────────────────────────────────────────────────────

func runOneShot(logger *slog.Logger, args []string) {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	configFile := fs.String("config", "", "path to JSON config file (required)")
	_ = fs.Parse(args)

	if *configFile == "" {
		fmt.Fprintln(os.Stderr, "usage: traffic-agent run --config <file>")
		os.Exit(1)
	}

	cfg, err := config.LoadOneShotConfig(*configFile)
	if err != nil {
		logger.Error("failed to load config", "err", err)
		os.Exit(1)
	}

	logger.Info("starting one-shot traffic test",
		"source", cfg.SourceDevice,
		"destination", cfg.DestDevice,
		"pattern", cfg.PatternType,
		"duration", cfg.Duration,
		"concurrent_users", cfg.ConcurrentUsers,
	)

	// Graceful shutdown on signal.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	startTime := time.Now()
	col := &metrics.Collector{}
	col.Reset()

	// Build pattern.
	pat, err := patterns.BuildFromOneShotConfig(cfg, nil)
	if err != nil {
		logger.Error("invalid pattern config", "err", err)
		os.Exit(1)
	}

	bps, _ := bandwidth.Parse(cfg.Bandwidth)
	numSessions := cfg.ConcurrentUsers
	if numSessions < 1 {
		numSessions = 1
	}

	protocol := strings.ToLower(cfg.Protocol)
	mgr := session.New(cfg.DestIP, cfg.Port, protocol, numSessions, pat, col, logger, cfg.SourceIP)

	duration := time.Duration(cfg.Duration) * time.Second
	if err := mgr.Run(ctx, duration); err != nil && ctx.Err() == nil {
		logger.Error("traffic generation failed", "err", err)
	}

	endTime := time.Now()
	snap := col.Snapshot()

	// Output results in the same JSON format the operator expects.
	result := map[string]interface{}{
		"test_name":       cfg.TestName,
		"source_device":   cfg.SourceDevice,
		"destination_device": cfg.DestDevice,
		"start_time":      startTime.UTC().Format(time.RFC3339),
		"end_time":        endTime.UTC().Format(time.RFC3339),
		"duration_sec":    cfg.Duration,
		"pattern_type":    cfg.PatternType,
		"bandwidth_target": cfg.Bandwidth,
		"concurrent_users": numSessions,
		"metrics": map[string]interface{}{
			"bytes_sent":       snap.BytesSent,
			"bytes_received":   snap.BytesReceived,
			"throughput_bps":   snap.ThroughputBps,
			"latency_ms":       snap.LatencyMs,
			"packet_loss_pct":  snap.PacketLossPct,
			"jitter_ms":        snap.JitterMs,
			"active_sessions":  snap.ActiveSessions,
		},
		"completed": true,
		"returncode": 0,
		// Keep "stdout" field for backward-compat with operator's result parsing.
		"stdout": fmt.Sprintf("traffic-agent: sent %d bytes in %s (%.2f Mbps)",
			snap.BytesSent,
			endTime.Sub(startTime).Round(time.Millisecond),
			float64(bps)/1e6,
		),
		"stderr": "",
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(result); err != nil {
		logger.Error("failed to encode results", "err", err)
		os.Exit(1)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// One-shot server mode: starts traffic receiver, exits after duration
// ─────────────────────────────────────────────────────────────────────────────

func runServe(logger *slog.Logger, args []string) {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	port := fs.Int("port", 5201, "port to listen on")
	protocol := fs.String("protocol", "TCP", "TCP or UDP")
	duration := fs.Int("duration", 60, "how long to serve traffic (seconds)")
	_ = fs.Parse(args)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	ctx, cancel := context.WithTimeout(ctx, time.Duration(*duration)*time.Second)
	defer cancel()

	col := &metrics.Collector{}
	col.Reset()

	logger.Info("starting traffic server",
		"port", *port,
		"protocol", *protocol,
		"duration", *duration,
	)

	var err error
	switch strings.ToUpper(*protocol) {
	case "TCP":
		srv := server.NewTCP(*port, col, logger)
		err = srv.Serve(ctx)
	case "UDP":
		srv := server.NewUDP(*port, col, logger)
		err = srv.Serve(ctx)
	default:
		logger.Error("unknown protocol", "protocol", *protocol)
		os.Exit(1)
	}

	if err != nil && ctx.Err() == nil {
		logger.Error("server error", "err", err)
		os.Exit(1)
	}

	snap := col.Snapshot()
	logger.Info("server done",
		"bytes_received", snap.BytesReceived,
		"throughput_bps", snap.ThroughputBps,
	)
}

func usage() {
	fmt.Fprintln(os.Stderr, `traffic-agent — network traffic generator and receiver

Usage:
  traffic-agent daemon  [--control-port PORT] [--metrics-port PORT]
  traffic-agent run     --config FILE
  traffic-agent serve   [--port PORT] [--protocol TCP|UDP] [--duration SECS]
  traffic-agent version

Modes:
  daemon   Long-lived HTTP agent.  POST /v1/flows to start flows.
           Default control port: 9090.
           Prometheus metrics served on a separate port (default 9091).
           The Ops Agent on the network VM scrapes GET /metrics on that port.

  run      One-shot source mode.  Reads a JSON config file and generates
           traffic to the destination, then outputs results JSON to stdout.
           Config format is identical to the Ansible-generated config_PORT.json.

  serve    One-shot destination mode.  Listens for traffic on the specified
           port and exits after duration seconds.  Replaces iperf3 -s.

Environment:
  LOG_LEVEL=debug   Enable debug-level structured logging.
`)
}
