-- Starred Sunsky categories
CREATE TABLE IF NOT EXISTS starred_sunsky_categories (
    id         SERIAL PRIMARY KEY,
    cat_id     VARCHAR(50) NOT NULL UNIQUE,
    name       VARCHAR(200) NOT NULL,
    parent_name VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
