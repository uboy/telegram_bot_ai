# zrag - Smart RAG Service Specification

## Overview

**zrag** is an intelligent Retrieval-Augmented Generation (RAG) service written in Rust. It provides document classification, smart chunking, vector-based semantic search with hybrid retrieval, and optional answer generation.

### Key Features
- **Smart Document Classification** - LLM-based classification into predefined document types
- **Adaptive Chunking** - Different chunking strategies per document class (AST-aware for code, semantic for text)
- **Hybrid Search** - Vector similarity + BM25 keyword search with RRF score fusion
- **Version Control** - Document versions with chunk history
- **Web UI** - Vue 3 SPA for document management and search
- **REST API** - OpenAPI-documented API for programmatic access

---

## Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                           zrag Service                               │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │   Web UI     │  │   REST API   │  │  WebSocket   │               │
│  │  (Vue 3)     │  │   (Axum)     │  │  (Progress)  │               │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘               │
│         │                 │                 │                        │
│  ┌──────┴─────────────────┴─────────────────┴───────┐               │
│  │                  Core Service                     │               │
│  │  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ │               │
│  │  │ Classifier  │  │   Chunker   │  │  Embedder │ │               │
│  │  │   (LLM)     │  │ (AST/Token) │  │ (Hybrid)  │ │               │
│  │  └─────────────┘  └─────────────┘  └───────────┘ │               │
│  │  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ │               │
│  │  │  Retriever  │  │  Reranker   │  │ Generator │ │               │
│  │  │  (Hybrid)   │  │ (Optional)  │  │ (Optional)│ │               │
│  │  └─────────────┘  └─────────────┘  └───────────┘ │               │
│  └───────────────────────────────────────────────────┘               │
│                              │                                       │
│  ┌───────────────────────────┴───────────────────────┐              │
│  │                    Data Layer                      │              │
│  │  ┌─────────────┐              ┌─────────────────┐ │              │
│  │  │   SQLite    │              │     Qdrant      │ │              │
│  │  │ (Metadata)  │              │   (Vectors)     │ │              │
│  │  └─────────────┘              └─────────────────┘ │              │
│  └────────────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

### Project Structure (Cargo Workspace)

```
zrag/
├── Cargo.toml              # Workspace manifest
├── config/
│   └── default.toml        # Default configuration
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── crates/
│   ├── zrag-core/          # Core business logic
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── classifier/  # Document classification
│   │       ├── chunker/     # Chunking strategies
│   │       ├── embedder/    # Embedding generation
│   │       ├── retriever/   # Search & retrieval
│   │       ├── reranker/    # Optional reranking
│   │       ├── generator/   # Optional LLM generation
│   │       └── types/       # Shared types
│   ├── zrag-api/           # HTTP API (Axum)
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── routes/
│   │       ├── middleware/
│   │       ├── websocket/
│   │       └── openapi.rs
│   ├── zrag-storage/       # Storage abstraction
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── sqlite/      # SQLite implementation
│   │       └── qdrant/      # Qdrant client
│   └── zrag-cli/           # CLI tool (optional)
│       ├── Cargo.toml
│       └── src/
│           └── main.rs
├── web/                     # Vue 3 frontend
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── main.ts
│       ├── App.vue
│       ├── components/
│       ├── views/
│       └── api/
└── tests/
    ├── integration/
    └── fixtures/
```

---

## Core Components

### 1. Document Classification

**Purpose**: Determine document type to select appropriate chunking strategy.

**Approach**: LLM-based classification using OpenAI or Anthropic API.

**Supported Document Classes**:
| Class | Description | Chunking Strategy |
|-------|-------------|-------------------|
| `text` | Plain narrative text, articles, documentation | Semantic (sentence boundaries, ~512-1024 tokens) |
| `code` | Source code files | AST-aware (functions, classes, methods) |
| `table` | Tabular data (CSV, structured) | Row-based (~256-512 tokens) |
| `markdown` | Markdown with mixed content | Section-based with code block detection |
| `config` | Configuration files (TOML, YAML, JSON) | Block-based (logical sections) |
| `log` | Log files | Line-group based (~256 tokens) |
| `mixed` | Documents with multiple content types | Hybrid (detect regions, apply per-region) |

**Classification Flow**:
```
Document → Extract sample (first 2000 chars) → LLM classify → Document Class
```

**LLM Prompt Template**:
```
Classify this document into one of: text, code, table, markdown, config, log, mixed.

Document sample:
{content_sample}

Rules:
- code: Contains function definitions, class declarations, imports
- table: Structured rows with consistent delimiters
- markdown: Has markdown headers (##), lists, code blocks
- config: TOML/YAML/JSON structure with key-value pairs
- log: Timestamped entries, error levels
- text: Narrative prose without special structure
- mixed: Multiple distinct types in one document

Respond with only the class name.
```

### 2. Chunking Strategies

**Base Configuration** (adaptive by class):
| Class | Target Size | Overlap | Strategy |
|-------|-------------|---------|----------|
| `text` | 512-1024 tokens | 64 tokens | Sentence boundaries |
| `code` | Variable (function size) | 0 | AST nodes (tree-sitter) |
| `table` | 256-512 tokens | 1 row | Row groups |
| `markdown` | 512-1024 tokens | 64 tokens | Section headers |
| `config` | 256-512 tokens | 0 | Logical blocks |
| `log` | 256 tokens | 2 lines | Line groups |
| `mixed` | Variable | Variable | Per-region |

**AST-Aware Chunking (Code)**:

Uses tree-sitter for parsing. Supported languages via tree-sitter grammars:
- Rust, Python, JavaScript, TypeScript, Go, Java, C, C++
- Ruby, PHP, C#, Kotlin, Swift, Scala
- Shell (Bash), SQL, HTML, CSS
- And all other tree-sitter supported grammars

**Chunk Structure**:
```rust
struct Chunk {
    id: Uuid,
    document_id: Uuid,
    version: u32,
    content: String,
    start_offset: usize,
    end_offset: usize,
    token_count: usize,
    metadata: ChunkMetadata,
}

struct ChunkMetadata {
    document_class: DocumentClass,
    language: Option<String>,      // For code
    ast_node_type: Option<String>, // For code (function, class, etc.)
    line_start: usize,
    line_end: usize,
}
```

### 3. Embedding Generation

**Hybrid Approach**:
- **Development/Local**: Local model via ONNX runtime (e.g., all-MiniLM-L6-v2)
- **Production**: OpenAI API (text-embedding-3-small/large)

**Configuration** (user selects):
| Model | Dimensions | Provider | Use Case |
|-------|------------|----------|----------|
| `all-MiniLM-L6-v2` | 384 | Local (ONNX) | Dev/testing |
| `text-embedding-3-small` | 1536 | OpenAI | Production (cost-effective) |
| `text-embedding-3-large` | 3072 | OpenAI | Production (highest quality) |
| Custom | Variable | Configurable | User-provided |

**Embedding Trait**:
```rust
#[async_trait]
trait Embedder: Send + Sync {
    async fn embed(&self, text: &str) -> Result<Vec<f32>>;
    async fn embed_batch(&self, texts: &[String]) -> Result<Vec<Vec<f32>>>;
    fn dimensions(&self) -> usize;
}
```

### 4. Storage

#### SQLite (Metadata & Relations)

**Schema**:
```sql
-- Documents table
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    document_class TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Document versions
CREATE TABLE document_versions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_id, version)
);

-- Chunks table
CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    token_count INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chunks_document ON chunks(document_id, version);
CREATE INDEX idx_chunks_deleted ON chunks(is_deleted);

-- Processing jobs (for async)
CREATE TABLE processing_jobs (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(id),
    status TEXT NOT NULL, -- pending, processing, completed, failed
    progress REAL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### Qdrant (Vector Storage)

**Collection Schema**:
```json
{
  "collection_name": "zrag_chunks",
  "vectors": {
    "size": 1536,  // Configurable based on embedding model
    "distance": "Cosine"
  },
  "payload_schema": {
    "document_id": "keyword",
    "chunk_id": "keyword",
    "version": "integer",
    "document_class": "keyword",
    "language": "keyword",
    "content": "text",
    "is_deleted": "bool"
  }
}
```

### 5. Retrieval

**Hybrid Search Flow**:
```
Query → Embed → Vector Search (Qdrant)
             ↘
              → BM25 Search (in-memory or Tantivy)
             ↓
         RRF Fusion → Filter by metadata → Optional Rerank → Results
```

**Reciprocal Rank Fusion (RRF)**:
```rust
fn rrf_score(ranks: &[usize], k: f32) -> f32 {
    ranks.iter().map(|r| 1.0 / (k + *r as f32)).sum()
}
// Default k = 60
```

**Search Request**:
```rust
struct SearchRequest {
    query: String,
    top_k: usize,                    // Default: 10
    filters: Option<SearchFilters>,
    include_context: bool,           // Include prev/next chunks
    rerank: bool,                    // Enable reranking
    generate_answer: bool,           // Generate LLM answer
}

struct SearchFilters {
    document_classes: Option<Vec<DocumentClass>>,
    languages: Option<Vec<String>>,
    document_ids: Option<Vec<Uuid>>,
    date_from: Option<DateTime<Utc>>,
    date_to: Option<DateTime<Utc>>,
}
```

**Search Response**:
```rust
struct SearchResponse {
    results: Vec<SearchResult>,
    generated_answer: Option<String>,
    query_embedding_time_ms: u64,
    search_time_ms: u64,
}

struct SearchResult {
    chunk: Chunk,
    score: f32,
    context: Option<ChunkContext>,  // prev/next chunks
}

struct ChunkContext {
    previous: Option<Chunk>,
    next: Option<Chunk>,
}
```

### 6. Reranking (Optional)

**Cross-Encoder Reranking**:
- Retrieve top-K * 3 candidates
- Rerank with cross-encoder model
- Return top-K final results

**Configuration**:
```toml
[retrieval.reranking]
enabled = false  # Can be enabled per-request
model = "cross-encoder/ms-marco-MiniLM-L-6-v2"
top_k_multiplier = 3
```

### 7. Generation (Optional)

**LLM Answer Generation**:
When `generate_answer = true`, constructs prompt with retrieved chunks and generates answer.

**Supported Providers**:
- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude)

**Provider Trait**:
```rust
#[async_trait]
trait LLMProvider: Send + Sync {
    async fn classify(&self, prompt: &str) -> Result<String>;
    async fn generate(&self, prompt: &str, context: &[Chunk]) -> Result<String>;
}
```

---

## API Design

### REST Endpoints

#### Documents

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/documents` | Upload document |
| `GET` | `/api/v1/documents` | List documents |
| `GET` | `/api/v1/documents/{id}` | Get document details |
| `DELETE` | `/api/v1/documents/{id}` | Delete document (and all chunks) |
| `GET` | `/api/v1/documents/{id}/chunks` | List chunks for document |
| `DELETE` | `/api/v1/documents/{id}/chunks/{chunk_id}` | Delete specific chunk |

#### Search

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/search` | Semantic search |

#### Jobs (Processing Status)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/jobs/{id}` | Get job status |

#### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/ready` | Readiness probe |

### WebSocket

**Endpoint**: `ws://host/ws/jobs/{job_id}`

**Messages**:
```json
// Progress update
{
  "type": "progress",
  "job_id": "uuid",
  "progress": 0.45,
  "stage": "chunking",
  "message": "Processing chunk 45/100"
}

// Completion
{
  "type": "completed",
  "job_id": "uuid",
  "document_id": "uuid"
}

// Error
{
  "type": "error",
  "job_id": "uuid",
  "error": "Error message"
}
```

### OpenAPI/Swagger

Generated using `utoipa` crate. Swagger UI available at `/swagger-ui`.

---

## Configuration

### Format

TOML file with environment variable overrides.

**Priority**: ENV vars > config file > defaults

**ENV Variable Format**: `ZRAG__{SECTION}__{KEY}` (double underscore)

Example: `ZRAG__EMBEDDER__PROVIDER=openai`

### Default Configuration (`config/default.toml`)

```toml
[server]
host = "0.0.0.0"
port = 8080
request_timeout_secs = 300

[storage.sqlite]
path = "./data/zrag.db"

[storage.qdrant]
url = "http://localhost:6334"
collection = "zrag_chunks"

[embedder]
provider = "openai"  # openai, local
model = "text-embedding-3-small"
dimensions = 1536
batch_size = 100

[embedder.openai]
api_key = ""  # Set via ZRAG__EMBEDDER__OPENAI__API_KEY

[embedder.local]
model_path = "./models/all-MiniLM-L6-v2.onnx"

[classifier]
provider = "openai"  # openai, anthropic

[classifier.openai]
api_key = ""  # Shared with embedder if same
model = "gpt-4o-mini"

[classifier.anthropic]
api_key = ""
model = "claude-3-haiku-20240307"

[chunker]
default_size = 512
default_overlap = 64

[chunker.code]
use_ast = true
fallback_to_tokens = true

[retrieval]
hybrid = true
bm25_weight = 0.3
vector_weight = 0.7
default_top_k = 10

[retrieval.reranking]
enabled = false
model = "cross-encoder/ms-marco-MiniLM-L-6-v2"

[generator]
enabled = true
provider = "openai"
model = "gpt-4o-mini"

[limits]
max_file_size_mb = 10
max_chunk_size_tokens = 2048
min_chunk_size_tokens = 50

[logging]
level = "info"
format = "json"
```

---

## Web UI (Vue 3)

### Tech Stack
- Vue 3 (Composition API)
- Vite (build)
- TypeScript
- Tailwind CSS (styling)
- Pinia (state management)
- Vue Router

### Pages

1. **Dashboard** (`/`)
   - Overview stats (documents, chunks, storage)
   - Recent documents
   - Quick search

2. **Documents** (`/documents`)
   - Document list with filters
   - Upload button
   - Delete actions

3. **Document Detail** (`/documents/:id`)
   - Document metadata
   - Chunk list with preview
   - Delete individual chunks
   - View versions

4. **Search** (`/search`)
   - Search input
   - Filters panel
   - Results with highlighting
   - Context expansion

5. **Upload** (`/upload`)
   - Drag-and-drop upload
   - Progress indicator (WebSocket)
   - Classification result preview

### Embedding in Rust Binary

Frontend built and embedded using `rust-embed` crate:
```rust
#[derive(RustEmbed)]
#[folder = "web/dist"]
struct Assets;
```

Served by Axum at root path.

---

## Testing Strategy

### Test Coverage Target: 80%+

### Test Categories

#### 1. Unit Tests
- Chunking strategies (each class)
- Classification parsing
- RRF fusion logic
- Configuration parsing

#### 2. Integration Tests
- API endpoints (with mock storage)
- WebSocket communication
- Document lifecycle

#### 3. Snapshot Tests
- LLM mock responses
- Embedding vectors (for specific inputs)
- API response formats

### LLM Mocking

```rust
#[cfg(test)]
struct MockLLMProvider {
    responses: HashMap<String, String>,
}

impl MockLLMProvider {
    fn with_classification(class: &str) -> Self {
        // Return predefined classification
    }
}
```

### Test Fixtures

Store in `tests/fixtures/`:
- Sample documents (text, code, markdown, etc.)
- Expected chunk outputs
- Mock LLM responses

---

## Docker Configuration

### Dockerfile

```dockerfile
# Build stage
FROM rust:1.83-slim AS builder

WORKDIR /app
COPY . .

# Build frontend
RUN apt-get update && apt-get install -y nodejs npm
WORKDIR /app/web
RUN npm ci && npm run build

# Build Rust
WORKDIR /app
RUN cargo build --release

# Runtime stage
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/zrag-api /usr/local/bin/zrag
COPY --from=builder /app/config/default.toml /etc/zrag/config.toml

EXPOSE 8080
ENV ZRAG__SERVER__HOST=0.0.0.0

CMD ["zrag"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  zrag:
    build: .
    ports:
      - "8080:8080"
    environment:
      - ZRAG__STORAGE__QDRANT__URL=http://qdrant:6334
      - ZRAG__EMBEDDER__OPENAI__API_KEY=${OPENAI_API_KEY}
    volumes:
      - zrag-data:/app/data
    depends_on:
      - qdrant

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant-data:/qdrant/storage

volumes:
  zrag-data:
  qdrant-data:
```

---

## Implementation Phases

### Phase 1: MVP (Compiles & Runs)

**Goal**: Basic working system with minimal functionality.

**Deliverables**:
- [ ] Project structure (workspace setup)
- [ ] Configuration loading (TOML + ENV)
- [ ] SQLite storage (documents, chunks)
- [ ] Qdrant connection
- [ ] Basic REST API (upload, list, delete)
- [ ] Simple token-based chunking (no AST)
- [ ] OpenAI embeddings integration
- [ ] Vector-only search (no hybrid)
- [ ] Health endpoint
- [ ] Docker setup
- [ ] Basic tests (API, storage)

**Simplifications**:
- No classification (all documents = `text`)
- No Web UI (API only)
- No WebSocket (sync processing)
- No reranking
- No generation

### Phase 2: Document Classification

**Goal**: Smart document processing based on type.

**Deliverables**:
- [ ] LLM classification (OpenAI)
- [ ] Anthropic provider support
- [ ] AST-aware chunking (tree-sitter)
- [ ] Multiple chunking strategies
- [ ] Document class storage
- [ ] Metadata filters in search
- [ ] Classification unit tests

### Phase 3: Full Functionality

**Goal**: Complete feature set as specified.

**Deliverables**:
- [ ] Vue 3 Web UI
- [ ] WebSocket progress updates
- [ ] Async document processing
- [ ] Hybrid search (vector + BM25)
- [ ] Reranking (optional)
- [ ] Answer generation (optional)
- [ ] Document versioning
- [ ] Context window in responses
- [ ] PDF support
- [ ] OpenAPI/Swagger UI
- [ ] Comprehensive test coverage (80%+)
- [ ] Production Docker image

---

## Dependencies (Rust Crates)

### Core
- `tokio` - Async runtime
- `axum` - HTTP framework
- `tower` - Middleware
- `serde` / `serde_json` - Serialization
- `uuid` - Unique IDs
- `chrono` - DateTime handling

### Storage
- `sqlx` - SQLite async driver
- `qdrant-client` - Qdrant Rust client

### Embeddings & LLM
- `reqwest` - HTTP client for APIs
- `ort` - ONNX runtime for local models
- `tokenizers` - Tokenization

### Chunking
- `tree-sitter` - AST parsing
- `tree-sitter-*` - Language grammars

### Document Processing
- `pdf-extract` or `lopdf` - PDF parsing

### Configuration
- `config` - Configuration management
- `toml` - TOML parsing

### Web
- `rust-embed` - Static file embedding
- `tower-http` - HTTP utilities (CORS, compression)
- `tokio-tungstenite` - WebSocket

### Testing
- `insta` - Snapshot testing
- `mockall` - Mocking
- `wiremock` - HTTP mocking

### Observability
- `tracing` - Structured logging
- `tracing-subscriber` - Log formatting

### API Docs
- `utoipa` - OpenAPI generation
- `utoipa-swagger-ui` - Swagger UI

---

## Non-Functional Requirements

### Performance
- Search latency: < 200ms (P95) for 100K chunks
- Document processing: < 30s for 10MB file
- Concurrent users: 100+

### Reliability
- Graceful shutdown
- Job recovery on restart
- Transaction safety for DB operations

### Security (Post-MVP)
- API key authentication
- Rate limiting
- Input validation
- No secrets in logs

### Scalability (Future)
- Multi-tenant by namespace
- Horizontal scaling (stateless API)
- Qdrant sharding

---

## Glossary

| Term | Definition |
|------|------------|
| **Chunk** | A segment of a document used for embedding and retrieval |
| **Embedding** | Vector representation of text for semantic search |
| **RAG** | Retrieval-Augmented Generation |
| **RRF** | Reciprocal Rank Fusion - method to combine search results |
| **AST** | Abstract Syntax Tree - structured code representation |
| **BM25** | Best Match 25 - classical text retrieval algorithm |
| **Cross-encoder** | Model that directly scores query-document pairs |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2024-XX-XX | - | Initial specification |
