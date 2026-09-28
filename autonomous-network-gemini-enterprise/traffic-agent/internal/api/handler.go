// Copyright 2024-2028 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package api provides the HTTP/JSON control API for the traffic agent daemon.
//
// Endpoints:
//
//	GET  /v1/health             — liveness probe
//	POST /v1/flows              — start a new flow (source or destination)
//	GET  /v1/flows              — list all flows
//	GET  /v1/flows/{id}         — get status of a specific flow
//	DELETE /v1/flows/{id}       — stop a running flow
package api

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/config"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/flowmanager"
)

const agentVersion = "1.0.0"

// Handler holds the HTTP mux for the agent control API.
type Handler struct {
	mgr    *flowmanager.Manager
	logger *slog.Logger
	mux    *http.ServeMux
}

// New creates an API Handler wired to the given flow manager.
func New(mgr *flowmanager.Manager, logger *slog.Logger) *Handler {
	if logger == nil {
		logger = slog.Default()
	}
	h := &Handler{mgr: mgr, logger: logger, mux: http.NewServeMux()}
	h.mux.HandleFunc("/v1/health", h.handleHealth)
	h.mux.HandleFunc("/v1/flows", h.handleFlows)
	h.mux.HandleFunc("/v1/flows/", h.handleFlow)
	return h
}

// ServeHTTP implements http.Handler.
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	h.logger.Debug("api request", "method", r.Method, "path", r.URL.Path, "remote", r.RemoteAddr)
	h.mux.ServeHTTP(w, r)
}

// GET /v1/health
func (h *Handler) handleHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{
		"status":  "ok",
		"version": agentVersion,
	})
}

// POST /v1/flows    — start flow
// GET  /v1/flows    — list all flows
func (h *Handler) handleFlows(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
		h.startFlow(w, r)
	case http.MethodGet:
		writeJSON(w, http.StatusOK, h.mgr.Status(""))
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// GET    /v1/flows/{id}  — status
// DELETE /v1/flows/{id}  — stop
func (h *Handler) handleFlow(w http.ResponseWriter, r *http.Request) {
	// Extract flow ID from path: /v1/flows/{id}
	parts := strings.SplitN(strings.TrimPrefix(r.URL.Path, "/v1/flows/"), "/", 2)
	flowID := parts[0]
	if flowID == "" {
		writeError(w, http.StatusBadRequest, "missing flow id in path")
		return
	}

	switch r.Method {
	case http.MethodGet:
		statuses := h.mgr.Status(flowID)
		if len(statuses) == 0 {
			writeError(w, http.StatusNotFound, "flow not found")
			return
		}
		writeJSON(w, http.StatusOK, statuses[0])

	case http.MethodDelete:
		if err := h.mgr.StopFlow(flowID); err != nil {
			writeError(w, http.StatusNotFound, err.Error())
			return
		}
		h.logger.Info("flow stopped via API", "flow_id", flowID, "remote", r.RemoteAddr)
		writeJSON(w, http.StatusOK, map[string]string{"status": "stopped", "flow_id": flowID})

	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (h *Handler) startFlow(w http.ResponseWriter, r *http.Request) {
	var req config.FlowRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON: "+err.Error())
		return
	}

	// Validate required fields.
	if req.FlowID == "" {
		h.logger.Warn("start flow rejected: missing flow_id", "remote", r.RemoteAddr)
		writeError(w, http.StatusBadRequest, "flow_id is required")
		return
	}
	if req.Role != "source" && req.Role != "destination" {
		h.logger.Warn("start flow rejected: invalid role", "role", req.Role, "flow_id", req.FlowID, "remote", r.RemoteAddr)
		writeError(w, http.StatusBadRequest, "role must be 'source' or 'destination'")
		return
	}
	if req.Port == 0 {
		h.logger.Warn("start flow rejected: missing port", "flow_id", req.FlowID, "remote", r.RemoteAddr)
		writeError(w, http.StatusBadRequest, "port is required")
		return
	}
	if req.DurationSec <= 0 {
		req.DurationSec = 60 // default 1 minute
	}
	if req.Protocol == "" {
		req.Protocol = "TCP"
	}

	if err := h.mgr.StartFlow(&req); err != nil {
		h.logger.Warn("start flow conflict", "flow_id", req.FlowID, "err", err)
		writeError(w, http.StatusConflict, err.Error())
		return
	}

	h.logger.Info("flow started",
		"flow_id", req.FlowID,
		"role", req.Role,
		"port", req.Port,
		"protocol", req.Protocol,
		"pattern", req.PatternType,
		"duration_sec", req.DurationSec,
		"remote", r.RemoteAddr,
	)
	writeJSON(w, http.StatusCreated, map[string]string{
		"status":  "started",
		"flow_id": req.FlowID,
	})
}

// --- helpers ---

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}
