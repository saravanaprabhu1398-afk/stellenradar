-- Posting and CV embeddings for matching (SDD §4.5, ADR-002). Titan Text Embeddings V2, 1024 dimensions.
CREATE TABLE vec.posting_vectors (
  refnr        text PRIMARY KEY,
  content_hash text NOT NULL,
  model_id     text NOT NULL,
  embedding    vector(1024) NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX posting_vectors_hnsw ON vec.posting_vectors USING hnsw (embedding vector_cosine_ops);

CREATE TABLE vec.cv_vector (
  id         smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  model_id   text NOT NULL,
  embedding  vector(1024) NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
