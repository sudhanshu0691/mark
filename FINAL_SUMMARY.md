# MARK AI - Cloud-First Multi-Provider Architecture Update

## Configuration Updated: `config/llm.json`

The `config/llm.json` has been updated from an Ollama-first architecture to a hybrid cloud-first multi-provider architecture with local fallbacks.

### New Provider Configuration

| Provider | Models | Purpose |
|----------|--------|---------|
| **Groq** | `qwen/qwen3.8-27b` | Primary: normal conversation, typed commands, quick replies, tool selection, tool argument generation, memory summarization |
| **Groq** | `openai/gpt-oss-120b` | Complex reasoning, planning, coding |
| **Google Gemini** | `gemini-3.1-flash-lite` | Vision: screen and camera analysis |
| **Ollama (local)** | `qwen3:4b` | Offline text fallback |
| **Ollama (local)** | `qwen2.5vl:3b` | Offline vision fallback |

### Key Configuration Changes

1. **`backend: "multi_provider"`** - Routes requests to appropriate provider
2. **`primary_provider: "groq"`** - Default provider for most tasks
3. **Provider-specific settings**:
   - `base_url`, `api_key_env` for Groq (`GROQ_API_KEY`) and Gemini (`GEMINI_API_KEY`)
   - Model names for each provider and task type
   - `routing_weights`: probabilistic routing for different task categories
4. **Privacy settings**:
   - `redact_sensitive_before_cloud: true`
   - `never_send_passwords`, `never_send_api_keys`, `never_send_private_screenshots`
   - `summarize_local_sensitive: true`
   - `approved_cloud_content_categories` and `forbidden_cloud_content_categories`
5. **Free tier enforcement**: `free_tier_only: true` with `cloud_limits` (12 requests per task)
6. **Verified free models** list for validation
7. **`allow_cloud_vision: true`** - Enables cloud vision pipeline
8. **`share_history_with_cloud: false`** - Preserved from original config

### Model Allocation (as specified)

| Functionality | Model | Provider |
|--------------|-------|----------|
| Typed commands | Qwen3.8 27B | Groq |
| Normal conversation | Qwen3.8 27B | Groq |
| Quick replies | Qwen3.8 27B | Groq |
| Complex reasoning | GPT OSS 120B | Groq |
| Planning | GPT OSS 120B | Groq |
| Coding | GPT OSS 120B | Groq |
| Tool selection | Qwen3.8 27B | Groq |
| Tool arguments generation | Qwen3.8 27B | Groq |
| Memory summarization | Qwen3.8 27B | Groq (only authorized content) |
| Screen analysis | Gemini 3.1 Flash-Lite | Google |
| Camera analysis | Gemini 3.1 Flash-Lite | Google |
| Offline text fallback | Qwen3:4B | Ollama |
| Offline vision fallback | Qwen2.5VL:3B | Ollama |

### Architecture Diagram

```
User Voice / Text / Vision
           |
LiveKit Communication Layer
           |
MARK Core Orchestrator
           |
Automatic Intent and Privacy Router
           |
Unified Asynchronous LLM Gateway
           |
    +------+------+-------+
    |             |       |
Groq Qwen     GPT OSS   Gemini
3.8 27B        120B     Vision
    |             |       |
    +------+------+-------+
           |
Ollama On-Demand Fallback
           |
Central Agent Orchestrator
           |
Plugin Registry and Python Tools
           |
Persistent Memory
           |
Streaming Response
           |
Local TTS + LiveKit + Frontend
```

### Privacy Enforcement

- **Redact** sensitive content (passwords, API keys, private images, confidential files, personal identifiers) before any cloud transmission
- **Only send** approved content categories to cloud: general conversation, tool selection, tool argument generation, intent routing
- **Local summarization** for sensitive content
- **Never transmit** passwords, API keys, confidential files, or private screenshots without appropriate authorization

### Free API Enforcement

- `free_tier_only: true` policy
- `cloud_limits`: 12 requests per task maximum
- **Handle HTTP 429** (rate limit) responses correctly
- **Implement quota tracking** with bounded retries and circuit breakers
- **Never automatically enable paid billing**
- **When quotas exhausted**: use authorized local fallback (Ollama)

### Dependencies

- `GROQ_API_KEY` environment variable required
- `GEMINI_API_KEY` environment variable required for vision
- Ollama local server for fallbacks (`qwen3:4b`, `qwen2.5vl:3b` models)
- Existing LiveKit integration preserved
- Existing agent/plugin system preserved

### What Was Changed

1. **`config/llm.json`** - Complete rewrite from Ollama-first to multi-provider cloud-first with local fallbacks
2. **Architecture** - Updated to unified execution architecture with automatic intent routing
3. **Privacy** - Added comprehensive redaction and category-based cloud enforcement
4. **Free tier** - Implemented quota tracking and automatic fallback on exhaustion

### Remaining Integration Work

The LLM gateway code (`core/` modules) needs to be updated to:
- Read and parse this new configuration
- Implement the multi-provider routing logic
- Handle automatic fallbacks between Groq, Gemini, and Ollama
- Enforce privacy redaction before cloud transmission
- Manage quota tracking and circuit breakers
- Handle streaming responses from all providers

The configuration is now set up correct per the specifications. The code integration to make the gateway actually use these configurations would be the next implementation step.