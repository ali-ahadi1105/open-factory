# Notion Clone (Next.js)

An interactive, block-based modular workspace and document manager built with Next.js, TypeScript, and Tailwind CSS. Every phase must include both robust functionality tests and comprehensive UI verification.

## Phase 1: Next.js Workspace Foundation, App Shell & Sidebar Navigation
Implement the foundational Next.js application shell, responsive collapsible sidebar, and document tree navigation.
Depends on: none
Labels: nextjs, ui, tested

### Acceptance Criteria:
- Next.js (App Router), TypeScript, Tailwind CSS, Lucide icons, and testing setup (Jest + React Testing Library).
- Notion-style App Shell layout:
  - Collapsible, resizable sidebar with Workspace Header (workspace title, settings button) and Quick Actions (New Page, Search, Trash).
  - Nested Page Tree navigation in the sidebar (expand/collapse chevron, active page highlighting, subpage add button).
  - Main content area with breadcrumbs navigation (`Workspace / Parent / Current Page`) and top toolbar.
- **Functionality Testing**:
  - Jest unit tests for sidebar state management (expand/collapse toggling, active route tracking, tree item expansion).
- **UI & Visual Verification**:
  - React Testing Library component tests asserting App Shell renders cleanly without layout shift.
  - Responsive layout verification (sidebar behaves properly on mobile and desktop).
  - Clean Notion-inspired styling (neutral palette, subtle borders, accessible contrast).

## Phase 2: Hierarchical Document Data Layer & Page Management (CRUD)
Implement document data modeling, persistent storage, recursive parent-child hierarchy, and page actions.
Depends on: Phase 1
Labels: nextjs, data, ui, tested

### Acceptance Criteria:
- Document model with TypeScript types (`id`, `title`, `icon`, `coverImage`, `parentId`, `isArchived`, `isFavorite`, `createdAt`, `updatedAt`).
- Document store service with optimistic client-side persistence (Local storage / Mock API).
- Document CRUD operations:
  - Create root pages and nested subpages.
  - Real-time page title editing with debounced auto-save.
  - Page Icon / Emoji picker popover.
  - Cover image selector with color presets and removal capability.
  - Soft-delete to Trash and Restore capability.
- **Functionality Testing**:
  - Unit tests for recursive document hierarchy (calculating breadcrumbs path, parent-child nesting).
  - State tests for CRUD operations, trash archive, and restoration.
- **UI & Visual Verification**:
  - Component tests for Document Header (editable title, icon button, cover banner).
  - Trash modal UI with search and restore buttons.
  - Action dropdown menu (Delete, Duplicate, Favorite) with proper keyboard accessibility.

## Phase 3: Modular Block-Based Content Editor with Slash Commands
Implement the core Notion-like block-based editing canvas with slash command menu and keyboard navigation.
Depends on: Phase 2
Labels: nextjs, editor, ui, tested

### Acceptance Criteria:
- Block-based document editing engine supporting core blocks:
  - Paragraph (body text)
  - Heading 1, Heading 2, Heading 3
  - Todo list with interactive checkbox and strikethrough text
  - Bulleted and Numbered lists
  - Code block with monospace typography and copy button
  - Callout box with custom emoji/icon and highlighted background
  - Quote and Divider blocks
- Slash Command Menu (`/`):
  - Typing `/` in an empty block opens a floating command palette to insert or convert block types.
  - Keyboard navigation (arrow keys + Enter) to select block types.
- Keyboard navigation & shortcuts:
  - Pressing `Enter` creates a new block below.
  - Pressing `Backspace` on an empty block removes it and focuses the previous block.
- **Functionality Testing**:
  - Unit tests for block transformation (converting Paragraph to Heading, Todo, etc.).
  - State tests for block insertion, deletion, and order preservation.
  - Todo completion state toggling.
- **UI & Visual Verification**:
  - Component tests verifying slash menu opens on `/` keypress and displays all block types.
  - Active block styling and focus management tests.
  - Clean typography and block spacing matching Notion's UI.

## Phase 4: Full-Text Search, Favorites & Markdown Export / View Mode
Implement document search modal, favorites pinning section, export utilities, and read-only preview mode.
Depends on: Phase 2, Phase 3
Labels: nextjs, features, ui, tested

### Acceptance Criteria:
- Quick Search / Command Palette modal triggered by `Cmd+K` / `Ctrl+K` or search button:
  - Real-time search across document titles and block contents.
  - Keyboard-navigable result items with quick jump to selected document.
- Favorites Section in the sidebar displaying pinned documents.
- Export utilities & Preview mode:
  - Export document to standard Markdown (`.md`) format and JSON.
  - Toggle between Edit Mode and clean Read-Only / Preview Mode.
- **Functionality Testing**:
  - Search algorithm tests (fuzzy matching, title and content matching).
  - Markdown exporter unit tests (converting block trees into valid Markdown).
  - Favorites toggle and persistence tests.
- **UI & Accessibility Verification**:
  - Modal focus trapping and `Escape` key dismissal for search modal.
  - Preview mode component rendering and styling tests.
