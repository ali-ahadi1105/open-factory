# Smart Link Hub & Analytics Service

A lightweight, high-performance URL shortening and click analytics service with expiration rules, custom aliases, and QR code generation.

## Phase 1: Core URL Shortening Engine & Redirection
Implement the core URL shortening and redirection engine.
Depends on: none
Labels: backend, core

### Acceptance Criteria:
- Implement a robust Base62 encoder/decoder for generating collision-resistant 6-7 character short codes.
- SQLite or persistent data store schema for URLs (id, original_url, short_code, created_at).
- Implement endpoint `POST /api/shorten` accepting `{"url": "https://..."}` and returning `{"short_url": "...", "short_code": "..."}`.
- Implement endpoint `GET /{short_code}` returning HTTP 302 redirect to original URL, or HTTP 404 for unknown codes.
- Full input validation (reject invalid URLs, loopback/private IP SSRF protection).
- Unit and integration tests covering encoding, valid creation, redirection, and 404 edge cases.

## Phase 2: Click Event Tracking & Analytics Aggregator
Implement click logging and aggregate analytics for shortened URLs.
Depends on: Phase 1
Labels: backend, analytics

### Acceptance Criteria:
- Data model for click events (short_code_id, timestamp, referrer, user_agent, ip_hash).
- Record click event asynchronously on every redirect in `GET /{short_code}` without adding redirection latency.
- Implement endpoint `GET /api/analytics/{short_code}` returning:
  - Total clicks
  - Clicks grouped by date/day
  - Top referrers
  - Browser/Device breakdown
- Integration tests verifying clicks are accurately tracked and aggregated.

## Phase 3: Link Expiration Policies & Custom Vanity Aliases
Add TTL expiration dates and custom alias support.
Depends on: Phase 1
Labels: backend, features

### Acceptance Criteria:
- Allow optional `expires_at` timestamp in `POST /api/shorten`.
- If a link has expired, `GET /{short_code}` must return HTTP 410 Gone instead of redirecting.
- Allow optional `custom_alias` in `POST /api/shorten` (e.g. `GET /my-promo`).
- Validate custom alias (alphanumeric, 3-30 chars, collision checks returning HTTP 409 Conflict if already taken).
- Unit tests for expired links (HTTP 410) and custom alias uniqueness.

## Phase 4: QR Code Generation & Analytics Export
Generate visual QR codes for short links and allow CSV/JSON analytics export.
Depends on: Phase 2, Phase 3
Labels: backend, reporting

### Acceptance Criteria:
- Implement endpoint `GET /api/qr/{short_code}` returning an SVG or PNG QR code image pointing to the short URL.
- Implement endpoint `GET /api/analytics/{short_code}/export?format=csv|json` for downloading full raw click records.
- Comprehensive end-to-end test suite testing the full lifecycle: shorten -> QR -> redirect -> track -> export.
