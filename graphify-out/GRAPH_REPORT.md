# Graph Report - .  (2026-07-22)

## Corpus Check
- 325 files · ~296,105 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3568 nodes · 6352 edges · 242 communities (197 shown, 45 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 959 edges (avg confidence: 0.69)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- LLM Providers & Orchestration Errors
- Node CLI Runner (bin/)
- Schedule Models
- Orchestration Engine
- API v2 Scale Endpoints
- User Auth Routes
- Frontend API Proxy Routes
- OpenAPI Import
- Fake HTTP Test Fixtures
- Orchestration Models & Loop
- Tools Registry (MCP + Custom)
- Frontend NPM Dependencies
- Session Routes
- Scale Configuration
- Postgres Async Engine
- Top-Level NPM Package
- Scale Admin Routes
- Examples Import UI
- Vault File Store
- Chat Page UI
- Agents & DB Settings Tabs
- ReAct Context Injection
- Worker Heartbeat
- Setup Wizard (LLM Prompts)
- Model Data Loaders
- Code Search Tool
- Docker Sandbox Tool
- Settings Page & Logs UI
- Embedding Providers
- Setup Wizard (OS Install)
- Personal Details & Env
- Messaging Adapter Manager
- Orchestration Context Builder
- Usage Dashboard UI
- JSON Store Persistence
- Deployment & Provider Icons
- Setup Wizard (Integrations)
- API Key Auth
- API v1 Chat Endpoints
- Profiling
- V1 Auth Tests
- MCP Client Manager
- Agent Builder
- CLI Prerequisites
- Web Scraper Tool
- Builder Tools Dispatch
- Messaging Channel Routes
- V2 Deep Route Tests
- Stress Load Harness
- Config Sync (Redis to Postgres)
- Code Indexer
- Route CRUD Tests
- ReAct Engine Deep Tests
- DBs Settings UI
- Agent CRUD Routes
- Memory Store
- Orchestration Logger
- Chat Route Tests
- Orchestration Route Tests
- Workflow Step Nodes UI
- Prompt Cache
- Cache Tests
- FastAPI Server Bootstrap
- Schedule Logger
- Real Orchestration Tests
- Orchestration Step Coverage Tests
- Scale Dashboard UI
- TypeScript Config
- Setup Wizard (Backend Install)
- Logs Routes
- Import/Export Routes
- Workflow State (Postgres)
- CLI Self-Upgrade
- Messaging Adapter Base
- Agent Logger
- Platform Markdown Formatters
- WhatsApp Adapter
- Fake Postgres Fixtures
- Step Config Panel UI
- Cache Store
- Community 80
- Community 81
- Community 82
- Community 83
- Community 84
- Community 85
- Community 86
- Community 87
- Community 88
- Community 89
- Community 90
- Community 91
- Community 92
- Community 93
- Community 94
- Community 95
- Community 96
- Community 97
- Community 98
- Community 99
- Community 100
- Community 101
- Community 102
- Community 103
- Community 104
- Community 105
- Community 106
- Community 107
- Community 108
- Community 109
- Community 110
- Community 111
- Community 112
- Community 113
- Community 114
- Community 115
- Community 116
- Community 117
- Community 118
- Community 119
- Community 120
- Community 121
- Community 122
- Community 123
- Community 124
- Community 125
- Community 126
- Community 127
- Community 128
- Community 129
- Community 130
- Community 131
- Community 132
- Community 133
- Community 134
- Community 135
- Community 136
- Community 137
- Community 138
- Community 139
- Community 140
- Community 141
- Community 142
- Community 143
- Community 144
- Community 145
- Community 146
- Community 147
- Community 148
- Community 149
- Community 150
- Community 151
- Community 152
- Community 153
- Community 154
- Community 155
- Community 156
- Community 157
- Community 158
- Community 159
- Community 160
- Community 161
- Community 162
- Community 163
- Community 164
- Community 165
- Community 166
- Community 167
- Community 168
- Community 169
- Community 170
- Community 171
- Community 172
- Community 173
- Community 174
- Community 175
- Community 176
- Community 177
- Community 178
- Community 179
- Community 180
- Community 181
- Community 182
- Community 183
- Community 184
- Community 185
- Community 186
- Community 188
- Community 189
- Community 190
- Community 191
- Community 192
- Community 193
- Community 194
- Community 195
- Community 197
- Community 198
- Community 199
- Community 200
- Community 201
- Community 202
- Community 203
- Community 204
- Community 205
- Community 206
- Community 207
- Community 210
- Community 211
- Community 212
- Community 213
- Community 214
- Community 215
- Community 216
- Community 220
- Community 221
- Community 223
- Community 225
- Community 226
- Community 227
- Community 228
- Community 229
- Community 230
- Community 231
- Community 232
- Community 233
- Community 234
- Community 235
- Community 236
- Community 237
- Community 238
- Community 239

## God Nodes (most connected - your core abstractions)
1. `OrchestrationEngine` - 89 edges
2. `Orchestration` - 53 edges
3. `load_settings()` - 46 edges
4. `warn()` - 38 edges
5. `ok()` - 37 edges
6. `run_agent_step()` - 35 edges
7. `backendHeaders()` - 35 edges
8. `main()` - 35 edges
9. `AgentLogger` - 32 edges
10. `LLMError` - 32 edges

## Surprising Connections (you probably didn't know these)
- `Anthropic Claude icon` --conceptually_related_to--> `14+ LLM Providers`  [INFERRED]
  frontend/public/claude-ai-icon.svg → README.md
- `AWS Bedrock icon` --conceptually_related_to--> `14+ LLM Providers`  [INFERRED]
  frontend/public/aws-bedrock-icon.svg → README.md
- `DeepSeek icon` --conceptually_related_to--> `14+ LLM Providers`  [INFERRED]
  frontend/public/deepseek-logo-icon.svg → README.md
- `Google Gemini icon` --conceptually_related_to--> `14+ LLM Providers`  [INFERRED]
  frontend/public/google-gemini-icon.svg → README.md
- `Ollama (local models) icon` --conceptually_related_to--> `14+ LLM Providers`  [INFERRED]
  frontend/public/ollama-icon.svg → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Scale-Mode Distributed Stack** — readme_redis_job_queue, readme_arq_worker_fleet, readme_pgbouncer_pool, readme_s3_artifact_storage, readme_multi_tenant_quotas, readme_checkpoint_recovery [EXTRACTED 1.00]
- **LLM Provider Surface (icons + dispatch)** — frontend_public_aws_bedrock_icon, frontend_public_openai_chatgpt_icon, frontend_public_anthropic_claude_icon, frontend_public_deepseek_icon, frontend_public_google_gemini_icon, frontend_public_xai_grok_icon, frontend_public_ollama_local_icon, backend_core_llm_providers, readme_14_plus_llm_providers [INFERRED 0.85]
- **Messaging Channel Surface** — frontend_public_discord_icon, frontend_public_slack_icon, frontend_public_teams_icon, frontend_public_telegram_icon, frontend_public_whatsapp_icon, readme_built_in_scheduling_messaging, backend_core_scheduler [INFERRED 0.85]

## Communities (242 total, 45 thin omitted)

### Community 0 - "LLM Providers & Orchestration Errors"
Cohesion: 0.08
Nodes (52): detect_mode_from_model(), LLMError, Exception, Raised when an LLM call fails after all retries.      This propagates through, Detect the provider mode from a model name prefix.      Returns 'cloud' for Op, OrchestrationRun, BaseModel, A single execution instance of an orchestration. (+44 more)

### Community 1 - "Node CLI Runner (bin/)"
Cohesion: 0.06
Nodes (58): ask(), askChoice(), askDefault(), BACKEND_DIR, BACKEND_PORT, checkCmd(), checkPrerequisites(), countRequirements() (+50 more)

### Community 2 - "Schedule Models"
Cohesion: 0.07
Nodes (41): BaseModel, Pydantic models for the Schedules feature., Schedule, ScheduleCreate, ScheduleUpdate, create_schedule(), _get_manager(), get_schedule() (+33 more)

### Community 3 - "Orchestration Engine"
Cohesion: 0.07
Nodes (37): Orchestration, Top-level orchestration definition -- a workflow graph of steps., OrchestrationEngine, Orchestration, Runs an orchestration by walking through its step graph., Resume a failed or cancelled orchestration from where it stopped.          For, Load agent_id -> name mapping for context attribution., BaseModel (+29 more)

### Community 4 - "API v2 Scale Endpoints"
Cohesion: 0.08
Nodes (51): _check_global_queue_depth(), _check_rate_limit(), _check_tenant_quota(), _create_run_row(), _get_arq_redis(), _get_pg_session_factory(), _new_run_id(), _new_session_id() (+43 more)

### Community 5 - "User Auth Routes"
Cohesion: 0.05
Nodes (47): auth_status(), callback(), login(), LoginRequest, BaseModel, Authentication routes: Google OAuth + Synapse login gate., Returns whether login is enabled and fully configured., Validate username/password and return a signed JWT on success. (+39 more)

### Community 6 - "Frontend API Proxy Routes"
Cohesion: 0.08
Nodes (31): GET(), POST(), POST(), GET(), POST(), POST(), POST(), POST() (+23 more)

### Community 7 - "OpenAPI Import"
Cohesion: 0.06
Nodes (22): _base_url_from_spec(), _build_input_schema(), _param_schema(), parse_openapi_spec(), Any, Convert an OpenAPI 3.x / Swagger 2.0 spec into Synapse custom-tool dicts.  Eac, Convert an OpenAPI 3.x / Swagger 2.0 spec into a list of custom-tool dicts., Reduce an operationId / path to a snake_case tool name (alnum + underscore). (+14 more)

### Community 8 - "Fake HTTP Test Fixtures"
Cohesion: 0.08
Nodes (20): FakeResponse, install_httpx(), Any, Fake httpx client for provider-call tests.  Providers that talk over HTTP (Ope, Patch httpx.AsyncClient so post()/get() return the given response.      ``hand, A handler that returns each response in turn (for retry tests)., sequence_handler(), _noop() (+12 more)

### Community 9 - "Orchestration Models & Loop"
Cohesion: 0.07
Nodes (24): Pydantic models for the multi-agent orchestration system. Defines orchestration, OrchestrationRun, StepConfig, OrchestrationEngine -- walks a graph of steps, managing shared state, checkpoin, Core execution loop — shared between run() and resume()., # IMPORTANT: never yield human_input_required inside the, Resume a paused orchestration after human input., Resume a parent run whose nested sub-orchestration was paused at a human step. (+16 more)

### Community 10 - "Tools Registry (MCP + Custom)"
Cohesion: 0.07
Nodes (38): add_mcp_server(), build_docker_sandbox(), create_custom_tool(), create_custom_tools_bulk(), delete_custom_tool(), _delete_mcp_from_pg(), get_available_tools(), get_custom_tools() (+30 more)

### Community 11 - "Frontend NPM Dependencies"
Cohesion: 0.05
Nodes (39): dependencies, clsx, codemirror, @codemirror/lang-python, @codemirror/language, @codemirror/state, @codemirror/theme-one-dark, @codemirror/view (+31 more)

### Community 12 - "Session Routes"
Cohesion: 0.08
Nodes (35): delete_session(), get_session_history(), get_session_snapshot(), get_sessions(), Session history REST API.  GET  /api/sessions                          → list, List all persisted chat sessions, newest first., Return the full list of turns for a session., Return just the last response and timestamp. (+27 more)

### Community 13 - "Scale Configuration"
Cohesion: 0.09
Nodes (26): get_scale_config(), Scale mode configuration — reads from settings and environment variables. Retur, Build ScaleConfig from settings + environment variable overrides., ScaleConfig, OrchestrationRunDB, Publishes SSE events for an orchestration run to a Redis Stream., RunEventPublisher, _deliver_webhook_for_run() (+18 more)

### Community 14 - "Postgres Async Engine"
Cohesion: 0.07
Nodes (35): async_sessionmaker, AsyncEngine, test_postgres_connection(), build_engine(), build_session_factory(), get_session(), init_db(), AsyncSession (+27 more)

### Community 15 - "Top-Level NPM Package"
Cohesion: 0.08
Nodes (34): bin, synapse, bugs, url, description, engines, node, files (+26 more)

### Community 16 - "Scale Admin Routes"
Cohesion: 0.10
Nodes (34): create_tenant(), delete_tenant(), get_scale_config_route(), get_scale_run(), list_dlq(), list_scale_runs(), list_tenants(), list_workers() (+26 more)

### Community 17 - "Examples Import UI"
Cohesion: 0.12
Nodes (27): ExamplePack, ExamplesView(), ExamplesViewProps, TAG_LABELS, TAG_STYLES, ExportView(), applyDefaultModels(), collectModels() (+19 more)

### Community 18 - "Vault File Store"
Cohesion: 0.15
Nodes (33): _build_tree(), create_vault_file(), create_vault_folder(), CreateFileRequest, CreateFolderRequest, delete_vault_item(), DeleteItemRequest, _get_storage() (+25 more)

### Community 19 - "Chat Page UI"
Cohesion: 0.10
Nodes (24): AgentStepResult(), AssistantBubble(), formatRelativeTime(), generateSessionId(), Home(), OrchBanner(), SessionSummary, SessionTurn (+16 more)

### Community 20 - "Agents & DB Settings Tabs"
Cohesion: 0.09
Nodes (23): AgentsTab(), AgentsTabProps, DatabaseTab(), DatabaseTabProps, DataLabTab(), DataLabTabProps, DbForm, EmbedCheckState (+15 more)

### Community 21 - "ReAct Context Injection"
Cohesion: 0.09
Nodes (28): load_settings(), _build_delegate_context(), _inject_db_context(), _inject_repo_context(), Async generator that runs the ReAct loop and yields structured events.      Th, Inject linked DB schema context into system prompt for code agents. Returns upda, Inject repo context into system prompt for code agents. Returns updated template, Build delegate agent context: load eligible sub-agents and inject the     synth (+20 more)

### Community 22 - "Worker Heartbeat"
Cohesion: 0.07
Nodes (23): mark_worker_offline(), Worker heartbeat — runs in the background on each worker process. Updates Postg, Continuously publish heartbeat at `interval` seconds until cancelled., Called during worker shutdown to mark the worker as offline in Postgres., API-server background task: marks workers offline when heartbeat goes silent., reap_stale_workers(), run_heartbeat(), ChatEventPublisher (+15 more)

### Community 23 - "Setup Wizard (LLM Prompts)"
Cohesion: 0.12
Nodes (30): ask(), ask_agent_name(), ask_choice(), ask_llm(), C, _detect_claude_cli(), _detect_codex_cli(), _detect_gemini_cli() (+22 more)

### Community 24 - "Model Data Loaders"
Cohesion: 0.08
Nodes (26): _bedrock_management_get(), clear_all_history(), clear_memory_items(), clear_recent_history(), _fetch_copilot_models(), get_bedrock_inference_profiles(), get_bedrock_models(), _get_github_token() (+18 more)

### Community 25 - "Code Search Tool"
Cohesion: 0.11
Nodes (28): call_tool(), _get_allowed_base_paths(), _get_chunks_for_file(), _get_table_name(), _glob_files(), _grep_file(), _grep_folder(), _is_path_allowed() (+20 more)

### Community 26 - "Docker Sandbox Tool"
Cohesion: 0.20
Nodes (28): _auto_name(), _build_docker_cmd(), call_tool(), _deep_merge(), _err(), _handle_create(), _handle_delete(), _handle_execute() (+20 more)

### Community 27 - "Settings Page & Logs UI"
Cohesion: 0.09
Nodes (20): LogsTab(), LogSummary, LogType, DraftServer, McpServersTab(), McpServersTabProps, McpToast, Preset (+12 more)

### Community 28 - "Embedding Providers"
Cohesion: 0.11
Nodes (28): embed_batch(), _embed_bedrock(), _embed_gemini(), _embed_ollama(), _embed_openai(), _embed_v1_compatible(), _hf_device(), _load_hf_model() (+20 more)

### Community 29 - "Setup Wizard (OS Install)"
Cohesion: 0.09
Nodes (29): add_to_bashrc(), _add_to_windows_path(), add_to_zshrc(), ask_startup_on_boot(), _gcloud_enable_apis(), _is_startup_registered_linux(), _is_startup_registered_mac(), _is_startup_registered_win() (+21 more)

### Community 30 - "Personal Details & Env"
Cohesion: 0.10
Nodes (23): default_personal_details(), load_personal_details(), save_personal_details(), get_example_bundle(), get_examples(), get_file(), get_personal_details_api(), get_settings() (+15 more)

### Community 31 - "Messaging Adapter Manager"
Cohesion: 0.08
Nodes (11): MessagingManager, Any, Return live status for all channels (merges store + runtime info)., Get the currently active agent for a chat, or None (use default)., Clear the per-chat agent selection (reset to channel default)., Switch the active agent for a chat by name.         Returns True if the agent w, Return all configured agents from the agent store., Run an agent's ReAct loop and return the final text response. (+3 more)

### Community 32 - "Orchestration Context Builder"
Cohesion: 0.09
Nodes (26): build_transition_context(), build_workflow_graph_markdown(), datetime_context(), _format_context_value(), _format_tool_calls(), get_execution_memory(), _origin_label(), Origin-aware context building for orchestration steps.  Provides:   - Transit (+18 more)

### Community 33 - "Usage Dashboard UI"
Cohesion: 0.12
Nodes (24): CacheDashboard(), CacheSummary, CompactionRow(), detectProvider(), EditablePricing, fmt$(), fmtDate(), fmtK() (+16 more)

### Community 34 - "JSON Store Persistence"
Cohesion: 0.16
Nodes (23): JsonStore, Args:             path: Path to the JSON file.             default_factory: Ca, AddMCPServerRequest, Agent, AgentActiveRequest, ChatResponse, DBConfig, GeneratePromptRequest (+15 more)

### Community 35 - "Deployment & Provider Icons"
Cohesion: 0.08
Nodes (27): Messaging-adapter dependencies (Slack/Discord/Telegram/Teams/WhatsApp), Worker-image dependencies (ARQ + scale runtime), docker-compose: pgbouncer (enterprise profile), docker-compose: redis (scale profile), docker-compose: worker (scale profile), Discord icon, Slack icon, Microsoft Teams icon (+19 more)

### Community 36 - "Setup Wizard (Integrations)"
Cohesion: 0.19
Nodes (26): ask_coding_agent(), ask_embed_code(), ask_google_workspace(), ask_ports(), ask_yn(), check_npm(), create_default_agent(), create_postgresql_db() (+18 more)

### Community 37 - "API Key Auth"
Cohesion: 0.13
Nodes (23): API Key Authentication Dependency ---------------------------------- FastAPI d, FastAPI dependency: validates Bearer token and returns the key record.      Ra, require_api_key(), delete_api_key(), generate_api_key(), _hash_key(), list_api_keys(), _load_keys() (+15 more)

### Community 38 - "API v1 Chat Endpoints"
Cohesion: 0.11
Nodes (24): ChatRequest, _build_chat_request(), _format_sse_event(), V1 External API Endpoints -------------------------- Programmatic API for exte, SSE streaming chat — returns all events., Submit human input and resume orchestration (SSE stream)., List all configured agents (id, name, type, capabilities)., Get details for a specific agent. (+16 more)

### Community 39 - "Profiling"
Cohesion: 0.11
Nodes (23): get_memory_snapshot(), get_stats(), is_cpu_profiling(), is_memory_profiling(), Performance profiling utilities for the Synapse backend.  - TimingMiddleware:, Return avg, p50, p95, p99, max, count per endpoint., Clear all timing data., reset_stats() (+15 more)

### Community 40 - "V1 Auth Tests"
Cohesion: 0.12
Nodes (9): TestApiKeyEdges, V1 external API auth + validation (all routes require a Bearer API key)., TestV1Auth, V1 external API — agent chat (sync + SSE). Auth via real API key fixture., _sse_json(), TestV1ChatStream, TestV1ChatSync, Seed one agent (default) or accept overrides via the returned factory. (+1 more)

### Community 41 - "MCP Client Manager"
Cohesion: 0.21
Nodes (11): AsyncClient, AsyncExitStack, MCPClientManager, _open_http_session(), Any, Try streamable HTTP (MCP 2025-03-26+) first, fall back to SSE (legacy).     Ret, Register a newly connected session into the global agent_sessions and tool_route, Connect to a remote MCP server with an optional pre-auth bearer token. (+3 more)

### Community 42 - "Agent Builder"
Cohesion: 0.13
Nodes (23): _apply_selected_model(), builder_chat(), builder_resume(), BuilderChatRequest, BuilderResumeRequest, _format_history(), BaseModel, Orchestration (+15 more)

### Community 43 - "CLI Prerequisites"
Cohesion: 0.14
Nodes (23): Popen, check_prerequisites(), _ensure_coding_deps(), ensure_data_dir(), _ensure_internal_token(), _ensure_node_in_path_win(), _find_node_exe_win(), _kill_proc_tree() (+15 more)

### Community 44 - "Web Scraper Tool"
Cohesion: 0.30
Nodes (21): AsyncWebCrawler, _build_run_config(), call_tool(), _err(), get_crawler(), _handle_crawl_multiple(), _handle_extract_links(), _handle_scrape_structured() (+13 more)

### Community 45 - "Builder Tools Dispatch"
Cohesion: 0.16
Nodes (22): _dispatch(), execute_builder_tool(), _fill_step_defaults(), _normalize_patch_arg(), _normalize_state_schema_arg(), _normalize_step_inputs(), _normalize_steps_arg(), _parse_json_field() (+14 more)

### Community 46 - "Messaging Channel Routes"
Cohesion: 0.15
Nodes (22): create_or_update_channel(), delete_channel(), disable_channel(), enable_channel(), _get_manager(), list_channels(), Request, REST API for messaging channel management. Includes webhook endpoints for Teams (+14 more)

### Community 47 - "V2 Deep Route Tests"
Cohesion: 0.12
Nodes (11): TestV2EventsAndCancel, TestV2ReadEndpoints, TestV2Resume, V2 status / events / cancel endpoints (Postgres reads via a fake session)., TestChatStatus, TestRunEvents, TestRunStatus, chat_row() (+3 more)

### Community 48 - "Stress Load Harness"
Cohesion: 0.12
Nodes (16): _pct(), Path, Async load harness for the stress suite.  Runs many task-coroutines through a, Execute ``total`` tasks (``task_factory(i)``) at most ``concurrency`` at a, _report_dir(), run_load(), _write_reports(), Stress: many concurrent agent chats through the REAL ReAct loop while the fake (+8 more)

### Community 49 - "Config Sync (Redis to Postgres)"
Cohesion: 0.16
Nodes (21): MCPServerDB, Key-value store for settings synced from settings.json.     Workers load LLM ke, SettingDB, ToolDB, full_sync(), get_sync_status(), _now_str(), AsyncSession (+13 more)

### Community 50 - "Code Indexer"
Cohesion: 0.18
Nodes (21): create_repo_flow(), drop_index(), _ensure_database_exists(), get_configured_embedding_model(), _get_current_vector_dim(), _get_db_url(), get_embedding_fn(), get_index_status() (+13 more)

### Community 51 - "Route CRUD Tests"
Cohesion: 0.09
Nodes (8): Broad CRUD + read coverage across the app route modules (settings, usage, sessi, TestCustomToolRoutes, TestDbConfigRoutes, TestHistoryRoutes, TestReposRoutes, TestSessionRoutes, TestSettingsRoutes, TestUsageRoutes

### Community 52 - "ReAct Engine Deep Tests"
Cohesion: 0.17
Nodes (11): Build a tool-call JSON string in the shape the ReAct engine parses.      Mirro, tool_call(), _drive(), Deep coverage of core.react_engine.run_agent_step: the tool-execution loop, rea, _server(), TestDelegateContext, TestErrorAndGuards, TestReasoningAndThought (+3 more)

### Community 53 - "DBs Settings UI"
Cohesion: 0.11
Nodes (17): ConfirmationModal(), ConfirmationModalProps, DB_TYPES, DBConfig, DBsTab(), ClearItem, DEFAULT_SELECTED, ITEMS (+9 more)

### Community 54 - "Agent CRUD Routes"
Cohesion: 0.15
Nodes (20): Agent, Load an agent dict by ID, falling back to the active agent., _resolve_agent_by_id(), build_agent(), _categorize_tools(), create_agent(), delete_agent(), get_active_agent_data() (+12 more)

### Community 55 - "Memory Store"
Cohesion: 0.12
Nodes (9): MemoryStore, Any, Store tool execution details for session-scoped retrieval.                  ID, Retrieve recent tool outputs for the current session.                  Returns, HYBRID search: exact text match FIRST, then semantic fallback., Public API wrapper around search_session_embeddings.                  Returns, Delete all session-scoped embeddings for cleanup.                  Called when, Generate a compact but informative summary of a large report. (+1 more)

### Community 56 - "Orchestration Logger"
Cohesion: 0.16
Nodes (11): _ensure_logs_dir(), _fmt_args(), OrchestrationLogger, Plain-text debug logging for orchestration runs. Appends human-readable entries, Process an SSE event and write relevant info to the log., Format tool arguments for log output., Appends debug lines to  data/orchestration_logs/<run_id>.log, Upload the completed log to S3 (scale mode). No-op in standalone mode. (+3 more)

### Community 57 - "Chat Route Tests"
Cohesion: 0.15
Nodes (8): chat(), Agent chat — internal app routes: POST /chat and POST /chat/stream.  Two layer, Seed a real agent, prime the tool cache so MCP introspection is         skipped, Parse SSE 'data:' JSON payloads from a streamed body., _sse_json(), TestChatRealEngine, TestChatStream, TestChatSync

### Community 58 - "Orchestration Route Tests"
Cohesion: 0.12
Nodes (11): _FakeEngine, Orchestration — internal app routes (core/routes/orchestrations.py).  CRUD is, Stand-in for OrchestrationEngine. Class-level ``events`` drives output., _sse_json(), TestOrchestrationCrud, TestOrchestrationResume, TestOrchestrationRun, fake_engine() (+3 more)

### Community 59 - "Workflow Step Nodes UI"
Cohesion: 0.14
Nodes (17): StepConfigPanelProps, ICONS, ROUTE_COLORS, StepNode, nodeTypes, ROUTE_COLORS, stepsToEdges(), stepsToNodes() (+9 more)

### Community 60 - "Prompt Cache"
Cohesion: 0.12
Nodes (19): decorate_anthropic_kwargs(), decorate_bedrock_system_blocks(), extract_anthropic_cache_tokens(), extract_gemini_cache_tokens(), is_cacheable_system(), Provider-payload decorators that turn on prompt caching.  Caching pricing is a, Gemini reports cached tokens in usage_metadata.cached_content_token_count., Append a cachePoint marker after the system text block.      Bedrock's Convers (+11 more)

### Community 61 - "Cache Tests"
Cohesion: 0.10
Nodes (8): cache_enabled(), Honor the global toggle. Defaults to True when the key is missing., Unit tests for the cache layer (prompt_cache, tool_cache, response_cache, store), personal_details is session-scoped — different sessions, different cache., Different surrounding metadata, same function name+params → same cache key., test_prompt_cache_global_toggle_default(), test_response_cache_key_normalises_tool_schema(), test_tool_cache_session_scope()

### Community 62 - "FastAPI Server Bootstrap"
Cohesion: 0.14
Nodes (16): Internal Token Middleware ------------------------- Protects all /api/* routes, _build_native_mcp_servers(), _connect_filesystem_mcp(), _filesystem_mcp_manager(), _get_google_oauth_env(), _get_repo_paths(), lifespan(), Extract OAuth client_id and client_secret from credentials.json for workspace-mc (+8 more)

### Community 63 - "Schedule Logger"
Cohesion: 0.16
Nodes (11): _ensure_logs_dir(), _fmt_args(), Path, Plain-text debug logging for individual schedule runs. Mirrors the design of ag, Process an SSE event and write relevant info to the log., Appends debug lines to logs/schedule_logs/<run_id>.log for a single schedule exe, Sync write -- only call from a thread (via _write_bg) or startup., Fire-and-forget write that offloads to a thread so the event loop isn't blocked. (+3 more)

### Community 64 - "Real Orchestration Tests"
Cohesion: 0.15
Nodes (9): Real orchestration runs through the HTTP routes (no engine patching): the inter, _sse_json(), TestRealAppRun, TestRealV1Run, V1 external API — orchestration run / run-stream / resume., _sse_json(), TestV1OrchestrationRunStream, TestV1OrchestrationRunSync (+1 more)

### Community 65 - "Orchestration Step Coverage Tests"
Cohesion: 0.19
Nodes (7): Broad coverage of the orchestration engine: every StepType executed through the, _run(), _server(), TestLoopAndParallel, TestPureSteps, TestTransformStep, _types()

### Community 66 - "Scale Dashboard UI"
Cohesion: 0.12
Nodes (14): AnalyticsData, copyToClipboard(), DEFAULT_CONFIG, DLQEntry, formatCost(), formatDuration(), QueueStats, RecentRun (+6 more)

### Community 67 - "TypeScript Config"
Cohesion: 0.10
Nodes (19): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+11 more)

### Community 68 - "Setup Wizard (Backend Install)"
Cohesion: 0.14
Nodes (20): _handle_already_installed(), install_backend(), _install_uv_unix(), check_uvx(), Add root_dir to the venv's site-packages via a .pth file.      This makes 'imp, Run a subprocess command with retries. Output flows to terminal so the user can, Wait for a server to be ready by polling HTTP, with a live elapsed counter., Stop any running Synapse services before rebuilding. (+12 more)

### Community 69 - "Logs Routes"
Cohesion: 0.11
Nodes (14): delete_schedule_log(), get_agent_log(), get_schedule_log(), list_agent_logs(), list_orchestration_logs(), list_schedule_logs(), Logs API: endpoints for agent run logs and orchestration run logs. Agent logs:, List recent agent run logs (summary only). (+6 more)

### Community 70 - "Import/Export Routes"
Cohesion: 0.22
Nodes (18): export_bundle(), ExportRequest, get_export_data(), import_bundle(), ImportRequest, _load_agents(), _load_custom_tools(), _load_mcp_servers() (+10 more)

### Community 71 - "Workflow State (Postgres)"
Cohesion: 0.16
Nodes (12): _dt_to_str(), _parse_dt(), Any, AsyncSession, datetime, OrchestrationRun, Postgres-backed SharedState — drop-in replacement for core/orchestration/state.p, Return a summary list of recent runs from Postgres. (+4 more)

### Community 72 - "CLI Self-Upgrade"
Cohesion: 0.12
Nodes (19): _download_and_apply_release(), _ensure_jwt_secret(), _fix_bin_permissions(), _get_current_version(), _get_latest_github_release(), _parse_version(), Return (tag_name, tarball_url) for the latest GitHub release, or (None, None) on, Download the release tarball and overwrite source files, preserving user data. (+11 more)

### Community 73 - "Messaging Adapter Base"
Cohesion: 0.14
Nodes (10): ABC, MessagingAdapter, Abstract base class for all messaging platform adapters. Every adapter must imp, Route an incoming message through the agent's ReAct loop.         Also checks i, Base adapter. Subclasses implement platform-specific connection logic.     Shar, Begin listening (polling loop or webhook registration)., Gracefully stop the listener., Platform-specific send. Called by send_message() after formatting. (+2 more)

### Community 74 - "Agent Logger"
Cohesion: 0.18
Nodes (9): AgentLogger, _ensure_logs_dir(), _fmt_args(), Plain-text debug logging for individual agent runs. Logs each call to an agent, Process an SSE event and write relevant info to the log., Appends debug lines to logs/agent_logs/<run_id>.log for a single agent execution, Fire-and-forget: enqueue for the background writer thread., Background thread: drains the write queue in order. (+1 more)

### Community 75 - "Platform Markdown Formatters"
Cohesion: 0.16
Nodes (16): _chunk(), format_for_platform(), Platform-native Markdown formatters. The agent produces standard Markdown. Befo, Teams supports a subset of Markdown in Bot Framework messages.     Strip unsupp, WhatsApp supports *bold*, _italic_, ~strikethrough~, `monospace`.     Strip eve, Split long text into chunks of at most `limit` chars, respecting lines., Format text for the given platform. Returns list of message chunks., Convert Markdown to Telegram MarkdownV2.     Returns a list of chunks (max 4096 (+8 more)

### Community 76 - "WhatsApp Adapter"
Cohesion: 0.16
Nodes (5): WhatsApp adapter with two paths:  Path A — Meta Cloud API (official, requires, Process an incoming webhook payload from Meta., Verify the Meta webhook subscription., # NOTE: Full message interception would require MutationObserver injection., WhatsAppAdapter

### Community 77 - "Fake Postgres Fixtures"
Cohesion: 0.12
Nodes (4): _FakeResult, _FakeSession, Any, A minimal async SQLAlchemy-session stand-in for V2 (distributed) tests.  The V

### Community 78 - "Step Config Panel UI"
Cohesion: 0.12
Nodes (6): pythonHighlight, STEP_TYPES, StepConfigPanel(), VaultFile, VaultTextarea(), VaultTextareaProps

### Community 79 - "Cache Store"
Cohesion: 0.18
Nodes (16): clear_namespace(), delete(), get(), _hash_key(), make_key(), _path_for(), Any, Path (+8 more)

### Community 80 - "Community 80"
Cohesion: 0.12
Nodes (13): InternalTokenMiddleware, BaseHTTPMiddleware, Request, Block direct access to internal /api/* routes without the internal token., BaseHTTPMiddleware, Request, Records per-request latency and adds X-Process-Time header. Always active., TimingMiddleware (+5 more)

### Community 81 - "Community 81"
Cohesion: 0.15
Nodes (13): Return MCP server configs — Postgres first (worker), JSON fallback., resolve_mcp_servers(), _build_remote_mcp_params(), _build_stdio_mcp_params(), _get_native_mcp_servers(), Path, Minimal stub of core.server's server_module interface for use inside worker proc, Satisfies the server_module interface expected by OrchestrationEngine and     s (+5 more)

### Community 82 - "Community 82"
Cohesion: 0.15
Nodes (9): get_tracer(), _instrument_libraries(), _NoOpSpan, _NoOpTracer, OpenTelemetry setup for the scale layer. No-op when OTLP_ENDPOINT is not config, Initialize OpenTelemetry SDK. Safe to call multiple times — only the first call, Apply auto-instrumentation to supported libraries., Return the configured tracer, or a no-op tracer if telemetry is disabled. (+1 more)

### Community 83 - "Community 83"
Cohesion: 0.18
Nodes (15): _ensure_local_path(), _make_vault_path(), Path, Vault: Automatically saves large tool outputs to files and provides tools to que, Return Path, rejecting obvious traversal attempts., If path doesn't exist locally but the file is a user vault file, try to download, Read lines [start_line, end_line] (1-indexed, inclusive) from any file., Generate a unique, safe vault file path. (+7 more)

### Community 84 - "Community 84"
Cohesion: 0.12
Nodes (6): Extra route coverage: vault CRUD, schedules CRUD, import/export, logs, api-keys, TestApiKeysAndMcp, TestImportExport, TestLogsRoutes, TestSchedulesCrud, TestVaultCrud

### Community 85 - "Community 85"
Cohesion: 0.17
Nodes (14): extract_openai_cache_tokens(), Return (cache_read_tokens, cache_write_tokens) from an OpenAI-style usage dict., _build_openai_image_content(), call_grok(), call_openai(), call_v1_compatible(), _openai_compat_extract(), Call xAI Grok via its OpenAI-compatible API with 5-attempt exponential backoff. (+6 more)

### Community 86 - "Community 86"
Cohesion: 0.21
Nodes (15): _build_exact_key(), _embed(), get_exact(), _get_memory_store(), get_semantic(), _get_semantic_collection(), LLM response cache — exact-match + optional semantic-match.  Exact match: SHA2, Resolve the live MemoryStore from server module (initialised at startup). (+7 more)

### Community 87 - "Community 87"
Cohesion: 0.20
Nodes (15): delete_channel(), get_channel(), get_channels_for_agent(), list_channels(), _load_raw(), JSON-backed persistent store for messaging channel configurations. Saved to DAT, Return all channel configs., Return one channel by id, or None. (+7 more)

### Community 88 - "Community 88"
Cohesion: 0.14
Nodes (13): drain_queue_with_heartbeat(), _inject_delegate_roster(), _is_tool_call(), parse_all_tool_calls(), Shared ReAct loop engine used by both /chat and /chat/stream endpoints. Yields, Single source of truth for tool-call extraction.      Strategy (in order):, Extract ALL tool-call JSON objects from one LLM response, in order., Inject a section into the system prompt listing available sub-agents     with t (+5 more)

### Community 89 - "Community 89"
Cohesion: 0.32
Nodes (15): BaseModel, V2ChatRequest, V2OrchestrationRunRequest, V2ResumeRequest, AgentDB, Base, ChatSessionDB, _now() (+7 more)

### Community 90 - "Community 90"
Cohesion: 0.23
Nodes (15): create_repo(), delete_repo(), get_repos(), load_repos(), BaseModel, Repo management endpoints (CRUD + reindex)., reindex_repo(), ReindexOptions (+7 more)

### Community 91 - "Community 91"
Cohesion: 0.19
Nodes (13): _decode(), get_chat_events(), get_run_events(), _is_stream_done(), EventBridge — bridges Redis Streams to FastAPI SSE endpoints.  Each API server, Same as stream_run_events but for chat session streams.     Reads from Redis St, Return all stored events for a run as a list of {id, event} dicts., Return all stored events for a chat session as a list of {id, event} dicts. (+5 more)

### Community 92 - "Community 92"
Cohesion: 0.13
Nodes (11): Webhook delivery with HMAC-SHA256 signing and exponential-backoff retry., call_tool(), list_tools(), main(), TextContent, Tool, List available time tools., Run the server using stdio transport. (+3 more)

### Community 93 - "Community 93"
Cohesion: 0.12
Nodes (7): Deeper route coverage across orchestrations, api_v1/v2 extras, settings, tools,, TestImportExportDeep, TestOrchestrationCrudDeep, TestSettingsDeep, TestToolsDeep, TestV1ResumeStream, TestVaultWrite

### Community 94 - "Community 94"
Cohesion: 0.12
Nodes (13): api_key(), client(), fake_llm(), _isolate_data(), Root test harness for the Synapse backend suite.  Critical ordering (mirrors b, The real FastAPI app, with a non-empty agent_sessions so /chat and the     ReAc, Async httpx client bound to the app via ASGITransport (no live server)., A real API key + ready-to-use Authorization header. (+5 more)

### Community 95 - "Community 95"
Cohesion: 0.12
Nodes (5): final(), gen_from(), Any, Helpers to fake the ReAct/orchestration engine at the route boundary.  Route-c, Return an async-generator *function* yielding the given events.      Usage:

### Community 96 - "Community 96"
Cohesion: 0.12
Nodes (6): BrandIconProps, ModelsTab(), ModelsTabProps, PROVIDER_META, ProviderInfo, ProviderMeta

### Community 97 - "Community 97"
Cohesion: 0.17
Nodes (13): applyStepHistory(), EMPTY_ORCHESTRATION, fetchRecoveredLog(), generateId(), LogEntry, newStep(), OrchestrationTab(), RecoveredLogEntry (+5 more)

### Community 98 - "Community 98"
Cohesion: 0.21
Nodes (14): create_or_update_orchestration(), delete_orchestration(), deploy_as_agent(), get_orchestration(), list_orchestrations(), list_runs(), load_orchestrations(), Orchestration (+6 more)

### Community 99 - "Community 99"
Cohesion: 0.18
Nodes (7): Prepend the configured prefix to a relative key path., Upload a UTF-8 string to S3. metadata values must be strings., Download a UTF-8 string from S3. Returns None if the key doesn't exist., Return S3 user metadata for a key. None if key not found., List all keys under rel_prefix (relative to the bucket prefix). Returns relative, Attempt a low-cost S3 operation to validate credentials and bucket access., SynapseS3

### Community 100 - "Community 100"
Cohesion: 0.19
Nodes (14): clear_usage_logs(), estimate_tokens_from_text(), get_cache_summary(), get_usage_summary(), _load_logs(), log_compaction_event(), LLM Usage & Cost Tracker ------------------------ Persists every LLM call's to, Rough heuristic: 1 token ? 4 characters. Used when the API doesn't return usage. (+6 more)

### Community 101 - "Community 101"
Cohesion: 0.21
Nodes (12): ActivityGroupProps, BUILDER_WELCOME_MESSAGE, BuilderPanel(), BuilderPanelProps, ChatMessage, isActivity(), markLastRunningComplete(), TYPE_COLORS (+4 more)

### Community 102 - "Community 102"
Cohesion: 0.17
Nodes (12): CustomToolsTab(), CustomToolsTabProps, OpenApiImport(), OpenApiImportProps, lintPython(), LintWarning, PACKAGES, PythonDraftTool (+4 more)

### Community 103 - "Community 103"
Cohesion: 0.15
Nodes (9): _convert_tools_for_anthropic(), detect_provider_from_model(), Detect the provider name from a model name prefix., Convert Ollama-format tool list to Anthropic tool format.      Ollama format:, get_status(), Unit tests for pure helpers in core.llm_providers (routing, conversion)., TestModeDetection, TestProviderDetection (+1 more)

### Community 104 - "Community 104"
Cohesion: 0.16
Nodes (12): _make_oauth_provider(), MCP Client Manager supporting two transport types:    stdio  — command-line su, complete_callback(), get(), pop(), Any, Shared in-memory store for pending OAuth flows.  Maps OAuth `state` parameter, Called by the OAuth callback route.  Returns True if state was found. (+4 more)

### Community 105 - "Community 105"
Cohesion: 0.22
Nodes (5): FileTokenStorage, Persists OAuth tokens and client registration to disk per server., OAuthClientInformationFull, OAuthToken, TokenStorage

### Community 106 - "Community 106"
Cohesion: 0.16
Nodes (9): Single forced tool call — lightweight direct LLM call (no full agent/ReAct stack, Execute a tool via MCP session or Docker sandbox (custom Python tools)., Execute a custom Python tool in the Docker sandbox (sandbox-python:latest)., Execute a custom HTTP tool with URL templating and method-aware arg routing., ToolStepExecutor, Global worker context — set once in worker_main.py at process startup.  When I, Return all custom tools — Postgres first (worker), JSON fallback., resolve_custom_tools() (+1 more)

### Community 107 - "Community 107"
Cohesion: 0.14
Nodes (6): V2 (distributed) API — HTTP contract tests.  V2 enqueues to Redis/ARQ and read, scale_app + a working fake PG factory + a neutralized metrics counter., TestScaleGating, TestV2Chat, TestV2OrchestrationRun, v2()

### Community 108 - "Community 108"
Cohesion: 0.18
Nodes (5): FakeLLM, Any, Scriptable fake LLM — the drop-in replacement for the real LLM call.  The sing, An async callable that stands in for ``generate_response``., Queue responses to be returned in order. Returns self for chaining.

### Community 109 - "Community 109"
Cohesion: 0.25
Nodes (12): check_git(), check_node(), detect_os(), _find_python_cmd(), _python_meets_minimum(), setup.sh script, check_python(), check_uvx() (+4 more)

### Community 110 - "Community 110"
Cohesion: 0.15
Nodes (10): Drain the write queue, then upload the completed log to S3 (scale mode)., delete_agent_log(), Delete a specific agent log., get_s3(), invalidate_s3_singleton(), S3 storage helper for scale mode. Provides a thin boto3 wrapper for vault files, Return the shared SynapseS3 instance, or None when S3 is not configured.     Re, Force the singleton to be rebuilt on next get_s3() call (e.g. after config save) (+2 more)

### Community 111 - "Community 111"
Cohesion: 0.17
Nodes (13): extract_deepseek_cache_tokens(), DeepSeek surfaces hit/miss separately., _build_cli_prompt(), call_deepseek(), call_huggingface(), generate_response(), _messages_to_transcript(), Call DeepSeek via its OpenAI-compatible API with 5-attempt exponential backoff. (+5 more)

### Community 112 - "Community 112"
Cohesion: 0.19
Nodes (12): Normalize a PostgreSQL URL for use with psycopg (not SQLAlchemy).      Fixes:, sanitize_db_url(), check_embed_setup(), Check if PostgreSQL + pgvector are correctly set up for code embedding., _get_pool(), _get_query_embedding(), Code indexer agent: search_codebase tool for vector similarity search. All inde, Generate an embedding vector for the search query using Gemini.     Uses the sa (+4 more)

### Community 113 - "Community 113"
Cohesion: 0.27
Nodes (11): Native Builder — Synapse's AI Builder implemented as a real orchestration.  Th, _apply_cheatsheet_to_agent(), _apply_cheatsheet_to_orch(), _expand_cheatsheet(), _load_bundled_agents(), _load_bundled_orchestration(), _load_json(), Any (+3 more)

### Community 114 - "Community 114"
Cohesion: 0.24
Nodes (6): Extra engine paths: run_agent_step orchestration parameters and additional step, _run(), _server(), TestMergeStrategies, TestRunAgentStepParams, TestStepEdgeCases

### Community 115 - "Community 115"
Cohesion: 0.19
Nodes (11): call_tool(), get_db_engine(), _is_write_query(), list_tools(), _load_db_configs(), EmbeddedResource, ImageContent, TextContent (+3 more)

### Community 116 - "Community 116"
Cohesion: 0.15
Nodes (10): DEST_DIR, { execSync }, FRONTEND_DIR, fs, path, publicSrc, ROOT, standaloneSrc (+2 more)

### Community 117 - "Community 117"
Cohesion: 0.18
Nodes (6): _import_adapter(), Messaging Manager — central lifecycle controller.  Responsibilities: - Load a, Stop and remove an adapter., Start all enabled channels from the store., Stop all running adapters., Load channel config, build adapter, and start it.

### Community 118 - "Community 118"
Cohesion: 0.17
Nodes (11): Restore run state from a checkpoint file., cancel_run(), get_run_status(), Request, Get the current state of a run from its checkpoint., Resume a failed or cancelled orchestration from where it stopped. Returns SSE st, Submit human input and resume the orchestration. Returns SSE stream.      For, Cancel a running orchestration (works for both V1 in-process and V2 distributed (+3 more)

### Community 119 - "Community 119"
Cohesion: 0.17
Nodes (11): clear_logs(), Usage & Cost API endpoints., Aggregate cost/token totals, grouped by model and session., Cache-focused aggregates for the Cache Analytics dashboard.      Returns total, Save an updated pricing table to model_pricing.json., Delete all usage logs., update_pricing(), usage_cache_summary() (+3 more)

### Community 120 - "Community 120"
Cohesion: 0.20
Nodes (12): _cache_rates(), calculate_cost(), calculate_savings(), _load_pricing(), log_usage(), USD saved on this call vs. paying full input rate for the cache_read tokens., Append a usage record to usage_logs.json (thread-safe).      `input_tokens` sh, Load the flat model_pricing.json. Returns {} on any error. (+4 more)

### Community 121 - "Community 121"
Cohesion: 0.23
Nodes (5): expand_vault_mentions(), maybe_vault(), If raw_output exceeds the vault threshold (from settings), persist it to vault a, Replace every @[relative/path] vault mention in the user message with the     f, TestVault

### Community 122 - "Community 122"
Cohesion: 0.17
Nodes (4): Remaining read endpoints: v1 list/get, v2 reads, orchestration estimate/runs, a, TestOrchestrationReads, TestToolAndSettingReads, TestV1ReadEndpoints

### Community 123 - "Community 123"
Cohesion: 0.23
Nodes (11): chat_stream_key(), collect_sse(), load_chat_events(), load_events(), load_run_events(), new_fake_redis(), Fake Redis Stream helpers for V2 SSE tests.  The V2 API streams events out of, A fresh in-memory async Redis (bytes responses, like the real client). (+3 more)

### Community 124 - "Community 124"
Cohesion: 0.21
Nodes (11): make_agent(), make_orchestration(), make_orchestrator_agent(), Any, Data seeders — write agents / orchestrations / API keys into the sandboxed SYNA, A minimal, valid conversational agent with no tools/repos/db., A single-step 'print' orchestration — runs with no LLM by default.      Caller, Create a real API key. Returns (raw_key, record). raw_key -> Bearer token. (+3 more)

### Community 125 - "Community 125"
Cohesion: 0.24
Nodes (11): _force_utf8_streams(), _is_running(), main(), _profile_command(), Best-effort: ensure console output uses UTF-8 so stray non-ASCII     characters, Terminate a process by PID, with fallback to SIGKILL., _read_pidfile(), _status_command() (+3 more)

### Community 126 - "Community 126"
Cohesion: 0.25
Nodes (8): _build_context_block(), _log_compaction(), _make_archive_path(), maybe_compact(), Path, Auto context compaction: when accumulated context exceeds a configurable thresho, Returns (context_text, history_messages, archive_path | None, compact_stats | No, TestCompaction

### Community 127 - "Community 127"
Cohesion: 0.31
Nodes (9): Thread-safe JSON file persistence with optional TTL caching. Replaces duplicate, create_db_config(), delete_db_config(), get_db_configs(), load_db_configs(), DB configuration management endpoints (CRUD + schema refresh)., refresh_db_schema(), save_db_configs() (+1 more)

### Community 129 - "Community 129"
Cohesion: 0.29
Nodes (6): Run sandboxed Python code to transform shared state., Build the JSON-in/JSON-out script wrapper used by both runtimes., Parse the JSON output of the wrapped script. Falls back to plain text., Run Python code directly on the host via subprocess (NO sandbox).          Use, Run Python code in the Docker sandbox (sandbox-python:latest)., TransformStepExecutor

### Community 130 - "Community 130"
Cohesion: 0.18
Nodes (4): Final robust route branches: import/export apply, vault folder ops, and setting, TestImportExportApply, TestSettingsEdges, TestVaultFolderOps

### Community 131 - "Community 131"
Cohesion: 0.22
Nodes (9): _package_json_version(), _pyproject_version(), Installation / packaging smoke tests (run in the gate).  Catches the breakages, PyPI (pyproject.toml) and npm (package.json) versions must match — the     rele, Importing every core.routes.* module guards against an import-time error     th, The `synapse` console script points at synapse.cli:main., test_all_route_modules_import_cleanly(), test_cli_entrypoint_exists() (+1 more)

### Community 132 - "Community 132"
Cohesion: 0.27
Nodes (5): Coverage for the remaining orchestration executors and engine control paths: th, _run(), _server(), TestEvaluatorStep, TestToolStep

### Community 133 - "Community 133"
Cohesion: 0.18
Nodes (4): Coverage for utility modules: usage_tracker, vault, session, config, personal_d, TestPersonalDetails, TestProfiling, TestUsageTracker

### Community 134 - "Community 134"
Cohesion: 0.24
Nodes (9): call_tool(), _get_allowed_dirs(), _is_allowed(), list_tools(), TextContent, Tool, Bash native tool — executes shell commands on the host system.  OS is detected, Return all directories the bash tool is allowed to run commands in.      Alway (+1 more)

### Community 135 - "Community 135"
Cohesion: 0.22
Nodes (9): call_tool(), CollectDataArgs, FieldDefinition, list_tools(), BaseModel, EmbeddedResource, ImageContent, TextContent (+1 more)

### Community 136 - "Community 136"
Cohesion: 0.25
Nodes (9): call_tool(), _ensure(), _grep(), list_tools(), TextContent, Tool, Lightweight file-reading MCP tool — safe for headless worker processes.  Expos, Resolve an S3-backed vault path to a local file if needed. (+1 more)

### Community 137 - "Community 137"
Cohesion: 0.20
Nodes (8): geistMono, geistSans, ibmPlexSans, inter, jetbrainsMono, metadata, StoreProvider(), store

### Community 138 - "Community 138"
Cohesion: 0.20
Nodes (11): _ensure_playwright_browsers(), _get_synapse_install_dir(), _load_dotenv(), Path, Minimal .env loader -- only sets vars that are NOT already in the environment., Return the platform-specific SynapseAI install directory written by setup.sh / s, Stop services and remove all Synapse AI files., Return the real system Python executable, not one inside a venv. (+3 more)

### Community 139 - "Community 139"
Cohesion: 0.20
Nodes (10): extract_bedrock_cache_tokens(), Bedrock returns cache metrics under response['usage']., _bedrock_converse_direct(), _build_anthropic_image_content(), call_bedrock(), _parse_data_uri(), Build Anthropic multimodal content blocks.      Returns list of content parts, Call Bedrock Converse via boto3 with UNSIGNED auth + before-send bearer injectio (+2 more)

### Community 140 - "Community 140"
Cohesion: 0.31
Nodes (9): clear_tool(), get(), is_cacheable(), _key(), Any, Deterministic tool-result memoization.  Only tools in DETERMINISTIC_TOOLS are, Return the cached tool result, or None if there's no live entry., Helper for manual invalidation (e.g. after the user re-indexes their codebase). (+1 more)

### Community 141 - "Community 141"
Cohesion: 0.24
Nodes (6): call_cli_provider(), _match_auth(), Spawn a local CLI binary, feed it the full context, and return the response., Return True only if `text` contains a specific auth-failure phrase.      Delib, Unit tests for CLI provider auth-failure detection (_match_auth).  Guards agai, TestAuthDetection

### Community 142 - "Community 142"
Cohesion: 0.20
Nodes (10): call_gemini(), _clean_schema_for_gemini(), _convert_messages_for_gemini(), _convert_tools_for_gemini(), _extract_gemini_response(), Remove fields from JSON schema that Gemini doesn't support., Convert OpenAI-style messages to Gemini Content objects.      Maps roles: 'use, Extract text and/or function call from a Gemini response.      When the model (+2 more)

### Community 143 - "Community 143"
Cohesion: 0.20
Nodes (4): Microsoft Teams adapter using Bot Framework SDK. Requires Azure Bot registratio, Teams uses an inbound webhook model. The adapter just validates credentials, Process an aiohttp request from the Teams webhook endpoint., TeamsAdapter

### Community 144 - "Community 144"
Cohesion: 0.22
Nodes (9): create_key(), CreateKeyRequest, list_keys(), BaseModel, API Key Management Endpoints ----------------------------- CRUD endpoints for, List all API keys (metadata only — no raw keys or hashes)., Generate a new API key.      The raw key is returned in this response ONLY — i, Delete an API key permanently. (+1 more)

### Community 145 - "Community 145"
Cohesion: 0.22
Nodes (4): Agent management routes (core/routes/agents.py): CRUD, active-agent, types., TestAgentsCrud, TestAgentTypes, _valid_agent_body()

### Community 146 - "Community 146"
Cohesion: 0.36
Nodes (3): TestAnthropicBody, TestGeminiBody, SimpleNamespace

### Community 147 - "Community 147"
Cohesion: 0.29
Nodes (6): Internal ReAct paths: auto-compaction firing inside the loop, deterministic too, _server(), TestCompaction, TestOrchestrationContextChain, TestToolCacheHit, _tool_schema()

### Community 148 - "Community 148"
Cohesion: 0.31
Nodes (9): Agent, CRON_PRESETS, describeCron(), describeSchedule(), emptyForm(), fmtTime(), Orchestration, Schedule (+1 more)

### Community 150 - "Community 150"
Cohesion: 0.33
Nodes (7): Route-surface smoke tests — "make sure nothing breaks".  Structural: assert th, All (path, methods) registered on the app, derived from the OpenAPI spec., _routes(), test_all_expected_prefixes_mounted(), test_critical_routes_registered(), test_no_duplicate_method_path_pairs(), test_route_surface_is_substantial()

### Community 151 - "Community 151"
Cohesion: 0.28
Nodes (6): _cfg(), V2 backpressure — the REAL rate-limit / queue-depth guards firing (previously m, scale_app with real backpressure helpers + fake PG + no-op metrics., TestQueueDepth, TestRateLimit, v2_real()

### Community 153 - "Community 153"
Cohesion: 0.25
Nodes (7): Channel, CREDENTIAL_FIELDS, EMPTY_CHANNEL, MessagingTab(), Platform, PLATFORMS, SETUP_GUIDES

### Community 154 - "Community 154"
Cohesion: 0.22
Nodes (3): MarkdownEditor(), VaultNode, VaultTab()

### Community 155 - "Community 155"
Cohesion: 0.25
Nodes (4): Create temporary embeddings for a report, scoped to current session., Return True if a column likely identifies individual rows., Create rich semantic text representation of a chunk for embedding., Fallback summary method when pandas not available.

### Community 156 - "Community 156"
Cohesion: 0.25
Nodes (5): extract_reasoning(), Return text with all [REASONING]...[/REASONING] blocks removed., Return the contents of every [REASONING] block, in order., strip_reasoning(), TestReasoningBlocks

### Community 157 - "Community 157"
Cohesion: 0.39
Nodes (3): parse_tool_call(), Extract the first tool-call JSON from one LLM response.      Returns (tool_cal, TestToolCallParsing

### Community 158 - "Community 158"
Cohesion: 0.39
Nodes (7): _get_n8n_config(), n8n_get_workflow_webhook(), n8n_list_workflows(), _n8n_request(), n8n integration endpoints (workflow listing, webhook discovery)., Lists workflows from n8n (requires n8n_url + n8n_api_key in settings)., Derives the production webhook URL for a workflow by locating a Webhook trigger

### Community 159 - "Community 159"
Cohesion: 0.25
Nodes (4): get_metrics_response(), Prometheus metrics for the scale layer. No-op when prometheus_client is not ins, Return (content, content_type) for the /metrics endpoint. Returns None if unavai, record_run_enqueued()

### Community 160 - "Community 160"
Cohesion: 0.25
Nodes (3): V2 SSE streaming via the Redis event bridge, backed by fakeredis.  Covers the, TestChatEventBridge, TestV2StreamEndpoints

### Community 161 - "Community 161"
Cohesion: 0.25
Nodes (6): call_tool(), list_tools(), EmbeddedResource, ImageContent, TextContent, Tool

### Community 162 - "Community 162"
Cohesion: 0.25
Nodes (6): call_tool(), list_tools(), EmbeddedResource, ImageContent, TextContent, Tool

### Community 163 - "Community 163"
Cohesion: 0.25
Nodes (6): call_tool(), list_tools(), EmbeddedResource, ImageContent, TextContent, Tool

### Community 164 - "Community 164"
Cohesion: 0.29
Nodes (8): get_linux_distro(), get_os_type(), install_pgvector(), install_postgresql(), Get OS type: 'linux', 'darwin', 'windows, Get Linux distribution type, Auto-install PostgreSQL if not found, Install pgvector extension in PostgreSQL

### Community 167 - "Community 167"
Cohesion: 0.38
Nodes (4): ExtractJsonStepExecutor, Extract JSON from text input (handles markdown fences, raw JSON, multiple object, Try multiple strategies to extract JSON from text., Find top-level {...} and [...] blocks by counting braces.

### Community 168 - "Community 168"
Cohesion: 0.33
Nodes (4): iter_with_heartbeat(), Yield items from `agen`, injecting `SSE_HEARTBEAT` whenever no item     arrives, Unit tests for the pure ReAct parsing helpers (no I/O, no LLM)., TestHeartbeat

### Community 169 - "Community 169"
Cohesion: 0.29
Nodes (5): V2 true end-to-end integration (nightly, real infrastructure).  Marked ``integ, Sanity: the event-bridge reader consumes what a producer XADDs to Redis.     A, Placeholder for the full path: POST /api/v2/orchestrations/{id}/run ->     ARQ, test_full_enqueue_to_stream(), test_redis_stream_roundtrip()

### Community 170 - "Community 170"
Cohesion: 0.29
Nodes (6): extra, fs, path, result, rootEnv, { spawnSync }

### Community 171 - "Community 171"
Cohesion: 0.29
Nodes (7): _find_all_node_versions_win(), _find_node_exe_win(), _find_npm_cmd_win(), Return the full path to npm.cmd on Windows., Windows-specific: probe all known Node.js install locations.     Returns list o, Return (node_exe, bin_dir) for the best Node >= 20.9.0 on Windows, else (None, N, start_frontend()

### Community 172 - "Community 172"
Cohesion: 0.33
Nodes (3): get_or_create_jwt_secret(), Return SYNAPSE_JWT_SECRET from the environment or .env file.      Persistence, TestConfig

### Community 173 - "Community 173"
Cohesion: 0.33
Nodes (6): estimate_orchestration_cost(), Estimate next-run cost based on the last N runs' usage logs.      Returns the, Paginated detailed per-call usage records, newest first., usage_logs(), get_usage_logs(), Return paginated usage records.     - When filtering by session_id or run_id: o

### Community 174 - "Community 174"
Cohesion: 0.33
Nodes (3): Gemini and Bedrock provider bodies, exercised with mocked SDK clients (no netwo, TestBedrockBody, TestHuggingFaceGuard

### Community 175 - "Community 175"
Cohesion: 0.47
Nodes (5): emptyDefaultFor(), StateSchemaEditor(), StateSchemaEditorProps, TYPES, StateSchemaEntry

### Community 176 - "Community 176"
Cohesion: 0.53
Nodes (5): AUTH_BYPASS_PREFIXES, config, proxy(), shouldBypassAuth(), verifyJwt()

### Community 177 - "Community 177"
Cohesion: 0.33
Nodes (6): _detect_github_copilot_cli(), _fetch_copilot_models_sync(), _get_github_token_sync(), Discover a GitHub token from env vars, gh CLI, or copilot config files (cross-pl, Fetch live model list from GitHub Models catalog API; falls back to hardcoded li, Returns GitHub Copilot CLI model list if 'copilot' binary is found, else [].

### Community 178 - "Community 178"
Cohesion: 0.47
Nodes (3): check_port(), start.sh script, wait_for_url()

### Community 179 - "Community 179"
Cohesion: 0.40
Nodes (5): GitHub Actions: CI Pipeline, GitHub Actions: Nightly Integration, Test-only dependencies (pytest, coverage, fakeredis), Deploy Gate (pytest fast suite), Fake LLM fixture (scriptable, no keys)

### Community 180 - "Community 180"
Cohesion: 0.40
Nodes (4): delete_orchestration_log(), Delete a specific orchestration log., delete_orchestration_log(), Delete a specific orchestration log.

### Community 181 - "Community 181"
Cohesion: 0.40
Nodes (4): get_orchestration_log(), Get full detailed log for a specific orchestration run (plain text)., get_orchestration_log(), Get full detailed log for a specific orchestration run (plain text).

### Community 183 - "Community 183"
Cohesion: 0.40
Nodes (3): Stress-suite configuration.  Activates the realistic-latency fake-LLM profile, Default to the 5-90s 'sometimes slow' profile; honor pre-set env values., stress_delay_profile()

### Community 184 - "Community 184"
Cohesion: 0.40
Nodes (3): BuildHookInterface, CustomBuildHook, Auto-build the Next.js frontend before packaging if not already built.

### Community 185 - "Community 185"
Cohesion: 0.40
Nodes (4): nextConfig, _parsedBackend, NOTE: SYNAPSE_INTERNAL_TOKEN and SYNAPSE_JWT_SECRET are intentionally NOT, _rootEnv

### Community 186 - "Community 186"
Cohesion: 0.40
Nodes (4): fs, os, path, SYNAPSE_HOME

### Community 188 - "Community 188"
Cohesion: 0.50
Nodes (3): chat_stream(), Chat endpoints: /chat and /chat/stream Thin wrappers around the shared ReAct en, Real-time streaming endpoint with SSE

### Community 189 - "Community 189"
Cohesion: 0.50
Nodes (4): Return the current pricing table from model_pricing.json., usage_pricing(), get_pricing_table(), Return the raw pricing table for the API.

### Community 190 - "Community 190"
Cohesion: 0.50
Nodes (4): _get_query_embedding(), Resolve the embedding model and dimension used for code search., Generate a query embedding using the same model and dimension as indexing., _resolve_query_embedding_config()

### Community 192 - "Community 192"
Cohesion: 0.50
Nodes (4): _get_default_install_dir(), _is_already_installed(), Return the OS-standard directory where Synapse AI should be installed., Return (True, install_dir) if a previous install is found, else (False, None).

### Community 193 - "Community 193"
Cohesion: 0.67
Nodes (3): GitHub Actions: Docker Build & Publish, docker-compose: backend service, docker-compose: frontend service

## Knowledge Gaps
- **308 isolated node(s):** `@playwright/test`, `{ spawnSync, spawn }`, `path`, `fs`, `crypto` (+303 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **45 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `lifespan()` connect `FastAPI Server Bootstrap` to `Schedule Models`, `MCP Client Manager`, `Orchestration Models & Loop`, `Scale Configuration`, `Postgres Async Engine`, `Scale Admin Routes`, `Community 113`, `Community 82`, `Code Indexer`, `ReAct Context Injection`, `Community 118`, `Worker Heartbeat`, `Personal Details & Env`, `Messaging Adapter Manager`?**
  _High betweenness centrality (0.084) - this node is a cross-community bridge._
- **Why does `MessagingManager` connect `Messaging Adapter Manager` to `Community 165`, `Community 166`, `Messaging Adapter Base`, `WhatsApp Adapter`, `Community 143`, `Community 80`, `Community 149`, `Community 117`, `FastAPI Server Bootstrap`?**
  _High betweenness centrality (0.079) - this node is a cross-community bridge._
- **Why does `load_settings()` connect `ReAct Context Injection` to `LLM Providers & Orchestration Errors`, `Community 129`, `User Auth Routes`, `Community 134`, `Tools Registry (MCP + Custom)`, `Scale Configuration`, `Scale Admin Routes`, `Model Data Loaders`, `Community 158`, `Personal Details & Env`, `Community 172`, `Config Sync (Redis to Postgres)`, `Code Indexer`, `Agent CRUD Routes`, `FastAPI Server Bootstrap`, `Community 190`, `Community 90`, `Community 103`, `Community 112`, `Community 115`, `Community 121`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Are the 71 inferred relationships involving `OrchestrationEngine` (e.g. with `TransitionContext` and `Orchestration`) actually correct?**
  _`OrchestrationEngine` has 71 INFERRED edges - model-reasoned connections that need verification._
- **Are the 50 inferred relationships involving `Orchestration` (e.g. with `OrchestrationEngine` and `AgentStepExecutor`) actually correct?**
  _`Orchestration` has 50 INFERRED edges - model-reasoned connections that need verification._
- **Are the 45 inferred relationships involving `load_settings()` (e.g. with `.execute()` and `.execute()`) actually correct?**
  _`load_settings()` has 45 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Plain-text debug logging for individual agent runs. Logs each call to an agent`, `Appends debug lines to logs/agent_logs/<run_id>.log for a single agent execution`, `Fire-and-forget: enqueue for the background writer thread.` to the rest of the system?**
  _1145 weakly-connected nodes found - possible documentation gaps or missing edges._