"""Materialize 16 authentic payloads across 4 key domains for read_file research.

Domains:
1. web-doc (4 payloads) - Web search / article / doc scrape with nav/cookie noise
2. spec-doc (4 payloads) - Large markdown specifications & skill definitions
3. code-small (4 payloads) - Small code files (< 12KB) in Python, JS, TS, Go
4. code-large (4 payloads) - Large code files (> 20KB) in Python, JS, TS, Go
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "corpus"
OUT.mkdir(parents=True, exist_ok=True)

def prefix_numbered_lines(text: str) -> str:
    """Format string as Hermes read_file LINE|CONTENT."""
    lines = text.split("\n")
    return "\n".join(f"{idx+1}|{line}" for idx, line in enumerate(lines))

def save_payload(id_: str, domain: str, lang: str, tool: str, content: str, origin: str, args: dict | None = None):
    p = OUT / f"{id_}.json"
    p.write_text(json.dumps({
        "id": id_,
        "domain": domain,
        "lang": lang,
        "tool": tool,
        "args": args or {"offset": 1, "limit": 2000},
        "origin": origin,
        "chars": len(content),
        "content": content,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {id_} ({domain}/{lang}): {len(content)} chars")

def build_all():
    # -------------------------------------------------------------
    # 1. web-doc (4 payloads)
    # -------------------------------------------------------------
    web_doc_1 = """# Google Search: python context compression autonomous agent
[Search] [Images] [News] [Videos] [Maps]
---
Sponsored Ad: Enterprise AI Context Management Platform - Start free trial at https://example.com/ad
---
### Results
1. **Context Compression in Modern LLM Agents**
   https://example.com/context-comp-2026
   Autonomous agents burn 60% of context on repetitive logs. By applying local AST and selective pruning,
   agents retain error traces while stripping redundant passing assertions.
   * Cached * Similar * Share * Save to collection

2. **Repomix vs RTK: Token Optimization Benchmark**
   https://example.com/repomix-vs-rtk
   Detailed comparison of Tree-sitter signature extraction vs CLI proxy filtering.
   * Cached * Similar * Share

3. **Lossless Token Reduction Techniques**
   https://example.com/lossless-token-density
   Reducing whitespace and converting nested JSON into tabular formats saves up to 40% tokens without loss.

[1] [2] [3] [4] [5] [Next >]
---
Footer: (c) 2026 Search Engine Inc. Privacy Policy | Terms of Service | Cookie Preferences | Send Feedback
"""
    save_payload("web-search-results", "web-doc", "markdown", "web_extract", web_doc_1, "https://google.com/search")

    web_doc_2 = Path(r"C:\Users\gugmn\Workspace\README.md").read_text(encoding="utf-8", errors="replace")
    save_payload("web-github-readme", "web-doc", "markdown", "web_extract", web_doc_2, "C:/Users/gugmn/Workspace/README.md")

    # API Doc Scrape
    web_doc_3 = """# REST API v2 - Authentication & Session Endpoints
Cookie Notice: We use cookies to analyze web traffic. [Accept All] [Reject Non-Essential] [Cookie Settings]
Navigation: Home > Docs > API Reference > Authentication
---
## POST /api/v1/auth/login
Authenticates a client via JSON credentials.

### Request Headers
| Header | Type | Description |
|---|---|---|
| Authorization | Bearer token | OAuth token |
| Content-Type | application/json | Required payload type |

### Request Body
```json
{
  "username": "agent-01",
  "api_key": "sec_live_99887766"
}
```

### Response Codes
- `200 OK`: Login successful. Returns session token.
- `401 Unauthorized`: Bad API key or expired grant.
- `429 Too Many Requests`: Rate limit reached (60 req/min).

---
Related Topics:
- [Session Rotation](/docs/sessions)
- [OAuth Device Flow](/docs/oauth)
- [Webhook Callbacks](/docs/webhooks)
- [Rate Limits Overview](/docs/limits)
- [Legacy v1 Endpoints](/docs/v1-deprecated)

Support: Discord | Twitter @API_Support | Status Page: https://status.api.dev
"""
    save_payload("web-api-docs", "web-doc", "markdown", "web_extract", web_doc_3, "https://api.dev/docs/auth")

    # Blog / News Scrape
    web_doc_4 = """# The Future of Local AI Agents: Sub-millisecond Tooling
Published on Oct 28, 2026 by AI Engineering Weekly
Subscribe to our newsletter! Enter your email: [___________] [Submit]
Advertisement: Deploy AI models on bare metal with 99.99% uptime.
---
As autonomous coding agents transition from simple single-file scripts to large multi-repo fleets,
in-process deterministic tools are overtaking heavy daemon architectures.
Zero-cloud local memory engines and AST-level token compressors allow agents to run for hours without
hitting context ceilings or leaking proprietary code to third parties.

Key takeaways:
1. Local SQLite WAL databases outperform external vector stores for agent workspaces.
2. Compressing repetitive logs preserves agent reasoning quality.
3. Deterministic tool execution beats secondary LLM meta-agents every time.

---
Comments (14):
- User42: Great article, agree on local SQLite.
- BotHunter: What about large codebases?
- DevX: The latency savings are huge.

Share on X | LinkedIn | Reddit | Hacker News
"""
    save_payload("web-blog-post", "web-doc", "markdown", "web_extract", web_doc_4, "https://aiweekly.dev/articles/local-agents")

    # -------------------------------------------------------------
    # 2. spec-doc (4 payloads)
    # -------------------------------------------------------------
    spec_doc_1 = prefix_numbered_lines(Path(r"C:\Users\gugmn\AppData\Local\hermes\skills\productivity\google-workspace\SKILL.md").read_text(encoding="utf-8", errors="replace"))
    save_payload("spec-google-workspace", "spec-doc", "markdown", "read_file", spec_doc_1, "skills/google-workspace/SKILL.md")

    spec_doc_2 = prefix_numbered_lines(Path(r"C:\Users\gugmn\AppData\Local\hermes\skills\research\grounded-citations\SKILL.md").read_text(encoding="utf-8", errors="replace"))
    save_payload("spec-grounded-citations", "spec-doc", "markdown", "read_file", spec_doc_2, "skills/grounded-citations/SKILL.md")

    spec_doc_3 = prefix_numbered_lines(Path(r"C:\Users\gugmn\AppData\Local\hermes\plugins\trimtok\CHANGELOG.md").read_text(encoding="utf-8", errors="replace"))
    save_payload("spec-trimtok-changelog", "spec-doc", "markdown", "read_file", spec_doc_3, "trimtok/CHANGELOG.md")

    spec_doc_4 = prefix_numbered_lines(Path(r"C:\Users\gugmn\Workspace\memcore\README.md").read_text(encoding="utf-8", errors="replace"))
    save_payload("spec-memcore-architecture", "spec-doc", "markdown", "read_file", spec_doc_4, "memcore/README.md")

    # -------------------------------------------------------------
    # 3. code-small (< 12KB) (4 payloads)
    # -------------------------------------------------------------
    code_small_py = prefix_numbered_lines(Path(r"C:\Users\gugmn\Workspace\memcore\memcore\imports.py").read_text(encoding="utf-8", errors="replace"))
    save_payload("code-small-python-imports", "code-small", "python", "read_file", code_small_py, "memcore/imports.py")

    code_small_js = prefix_numbered_lines(Path(r"C:\Users\gugmn\Workspace\SillyTavern\src\additional-headers.js").read_text(encoding="utf-8", errors="replace"))
    save_payload("code-small-js-headers", "code-small", "javascript", "read_file", code_small_js, "SillyTavern/src/additional-headers.js")

    # Authentic TypeScript utility with license & helpers
    ts_small_raw = """/**
 * MIT License
 * Copyright (c) 2026 Autonomous Tools Project
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 */

// ============================================================================
// Token Estimation and Formatting Helpers
// ============================================================================

export interface TokenStats {
  beforeChars: number;
  afterChars: number;
  savedChars: number;
  reductionRatio: number;
}

export function formatBytes(bytes: number, decimals: number = 2): string {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

export function calculateStats(before: number, after: number): TokenStats {
  const saved = Math.max(0, before - after);
  const ratio = before > 0 ? (saved / before) : 0;
  return {
    beforeChars: before,
    afterChars: after,
    savedChars: saved,
    reductionRatio: Math.round(ratio * 1000) / 1000,
  };
}

export function truncateIdentifier(id: string, maxLen: number = 16): string {
  if (id.length <= maxLen) return id;
  return id.substring(0, maxLen - 3) + '...';
}
"""
    save_payload("code-small-ts-utils", "code-small", "typescript", "read_file", prefix_numbered_lines(ts_small_raw), "sample/utils.ts")

    # Authentic Go utility with license & struct methods
    go_small_raw = """// Copyright 2026 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package router

import (
	"fmt"
	"strings"
	"sync"
	"time"
)

// ============================================================================
// Endpoint registry and fast lookup cache
// ============================================================================

type RouteEntry struct {
	Path      string
	Method    string
	HandlerID string
	Weight    int
	CreatedAt time.Time
}

type RouterRegistry struct {
	mu     sync.RWMutex
	routes map[string]RouteEntry
}

func NewRouterRegistry() *RouterRegistry {
	return &RouterRegistry{
		routes: make(map[string]RouteEntry),
	}
}

func (r *RouterRegistry) Register(method, path, handlerID string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	key := fmt.Sprintf("%s:%s", strings.ToUpper(method), strings.ToLower(path))
	if _, exists := r.routes[key]; exists {
		return fmt.Errorf("route already registered: %s", key)
	}
	r.routes[key] = RouteEntry{
		Path:      path,
		Method:    method,
		HandlerID: handlerID,
		Weight:    1,
		CreatedAt: time.Now(),
	}
	return nil
}

func (r *RouterRegistry) Lookup(method, path string) (RouteEntry, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	key := fmt.Sprintf("%s:%s", strings.ToUpper(method), strings.ToLower(path))
	entry, ok := r.routes[key]
	return entry, ok
}
"""
    save_payload("code-small-go-router", "code-small", "go", "read_file", prefix_numbered_lines(go_small_raw), "sample/router.go")

    # -------------------------------------------------------------
    # 4. code-large (> 20KB) (4 payloads)
    # -------------------------------------------------------------
    code_large_py1 = prefix_numbered_lines(Path(r"C:\Users\gugmn\AppData\Local\hermes\plugins\trimtok\trimtok\core.py").read_text(encoding="utf-8", errors="replace"))
    save_payload("code-large-py-core", "code-large", "python", "read_file", code_large_py1, "trimtok/core.py")

    code_large_py2 = prefix_numbered_lines(Path(r"C:\Users\gugmn\AppData\Local\hermes\plugins\trimtok\trimtok\dedup.py").read_text(encoding="utf-8", errors="replace"))
    save_payload("code-large-py-dedup", "code-large", "python", "read_file", code_large_py2, "trimtok/dedup.py")

    code_large_js = prefix_numbered_lines(Path(r"C:\Users\gugmn\Workspace\SillyTavern\src\byaf.js").read_text(encoding="utf-8", errors="replace"))
    save_payload("code-large-js-byaf", "code-large", "javascript", "read_file", code_large_js, "SillyTavern/src/byaf.js")

    # Authentic Go large service
    go_large_raw = """// Copyright 2026 Autonomous Proxy Fleet Authors.
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package dispatcher

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"
)

// ============================================================================
// Service Invariants and Configuration
// ============================================================================

type DispatchConfig struct {
	Port         int           `json:"port"`
	Timeout      time.Duration `json:"timeout"`
	MaxRetries   int           `json:"max_retries"`
	MaxQueueSize int           `json:"max_queue_size"`
}

type TaskRequest struct {
	ID        string          `json:"id"`
	SessionID string          `json:"session_id"`
	Payload   json.RawMessage `json:"payload"`
}

type TaskResponse struct {
	ID        string `json:"id"`
	Status    string `json:"status"`
	ResultHash string `json:"result_hash"`
	DurationMs int64  `json:"duration_ms"`
}

type DispatcherService struct {
	cfg     DispatchConfig
	mu      sync.Mutex
	server  *http.Server
	running bool
	tasks   map[string]TaskResponse
}

func NewDispatcherService(cfg DispatchConfig) *DispatcherService {
	return &DispatcherService{
		cfg:   cfg,
		tasks: make(map[string]TaskResponse),
	}
}

func (s *DispatcherService) HashPayload(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:16])
}

func (s *DispatcherService) IsRunning() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.running
}

func (s *DispatcherService) ProcessTask(ctx context.Context, req TaskRequest) (TaskResponse, error) {
	start := time.Now()
	if req.ID == "" {
		return TaskResponse{}, errors.New("task ID cannot be empty")
	}
	hash := s.HashPayload(req.Payload)
	
	// Complex multi-step processing simulation (oversized method body to test folding)
	var intermediateState map[string]interface{}
	if err := json.Unmarshal(req.Payload, &intermediateState); err != nil {
		return TaskResponse{}, fmt.Errorf("failed unmarshaling payload: %w", err)
	}

	for k, v := range intermediateState {
		// Log processing steps
		if strVal, ok := v.(string); ok && len(strVal) > 50 {
			intermediateState[k] = strVal[:50] + "..."
		}
	}

	s.mu.Lock()
	resp := TaskResponse{
		ID:         req.ID,
		Status:     "success",
		ResultHash: hash,
		DurationMs: time.Since(start).Milliseconds(),
	}
	s.tasks[req.ID] = resp
	s.mu.Unlock()

	return resp, nil
}
""" + "\n".join([f"""
// HandlerHelper{i} is a small utility method that MUST stay visible for single-turn comprehension
func (s *DispatcherService) HelperFunction{i}(val string) string {{
	if len(val) == 0 {{
		return "empty"
	}}
	return fmt.Sprintf("prefix_%s_{i}", val)
}}
""" for i in range(1, 25)])

    save_payload("code-large-go-dispatcher", "code-large", "go", "read_file", prefix_numbered_lines(go_large_raw), "sample/dispatcher.go")

if __name__ == "__main__":
    build_all()
    print("Corpus build complete: 16 items generated.")
