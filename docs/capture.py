# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright"]
# ///
"""
Capture screenshots of the Daily Task Manager for the README.
Run with:  uv run docs/capture.py
The server must be running on localhost:3456 before running this script.

Uses only mock/demo data — real task data on disk is never read or shown.
"""

from playwright.sync_api import sync_playwright
import time

BASE = "http://localhost:3456"
OUT  = "docs"
W, H = 1280, 820

# ---------------------------------------------------------------------------
# Mock data injected into the app's in-memory state for screenshots.
# This completely replaces whatever real data is loaded from disk.
# ---------------------------------------------------------------------------
MOCK_JS = """
() => {
    const uid = () => Math.random().toString(36).slice(2, 11);
    const task = (text, opts = {}) => ({
        id: uid(),
        text,
        status:    opts.status    ?? 'pending',
        important: opts.important ?? false,
        recurring: opts.recurring ?? false,
        closed:    opts.closed    ?? false,
        children:  opts.children  ?? [],
        context: {
            notes:       opts.notes       ?? [],
            links:       opts.links       ?? [],
            attachments: opts.attachments ?? [],
        },
    });

    const today = state.activeDay;

    // Build a realistic-looking but entirely fictional task list
    const mockTasks = [
        task('Write Q2 project proposal', {
            children: [
                task('Draft executive summary'),
                task('Define success metrics', { status: 'done' }),
                task('Estimate resource requirements'),
            ],
        }),
        task('Review pull requests', {
            status: 'partial',
            children: [
                task('Auth service refactor PR', { status: 'done' }),
                task('Data pipeline optimisation PR', { status: 'partial' }),
                task('Add integration tests PR'),
            ],
        }),
        task('Daily standup prep', {
            recurring: true,
            children: [
                task('Check team blockers'),
                task('Update progress notes'),
            ],
            context: {
                notes: [{ text: 'Remind team about Friday release freeze.' }],
                links: [
                    { type: 'Slack',  label: '#eng-standup',  url: 'https://example.com/slack' },
                    { type: 'Google Docs', label: 'Sprint notes', url: 'https://example.com/docs' },
                ],
                attachments: [],
            },
        }),
        task('Migrate legacy API endpoints', {
            important: true,
            children: [
                task('Audit existing endpoints', { status: 'done' }),
                task('Map deprecated routes', { status: 'partial', important: true }),
                task('Update client libraries'),
                task('Write migration guide'),
            ],
        }),
        task('Prepare monthly report', {
            children: [
                task('Gather KPI data'),
                task('Create charts'),
                task('Share with stakeholders'),
            ],
        }),
    ];

    state.days[today] = { tasks: mockTasks };
    renderTaskArea();
}
"""


def shot(page, name, label=""):
    if label:
        print(f"  → {label}")
    page.screenshot(path=f"{OUT}/{name}.png")


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox"]
        )
        ctx = browser.new_context(viewport={"width": W, "height": H})
        page = ctx.new_page()

        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        page.evaluate("() => new Promise(r => setTimeout(r, 800))")

        # Inject mock data — replaces real tasks in memory (disk untouched)
        page.evaluate(MOCK_JS)
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")

        # ── 1. Main view ──────────────────────────────────────────────────────
        page.evaluate("() => { expandAll(); closeContextPanel(); closeSummaryModal(); }")
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "01-main-view", "Main task view")

        # ── 2. Task actions on hover ──────────────────────────────────────────
        page.evaluate("() => { expandAll(); closeContextPanel(); }")
        first_row = page.locator(".task-row").first
        first_row.hover()
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "02-task-actions", "Task actions on hover")

        # ── 3. Add subtask ────────────────────────────────────────────────────
        page.evaluate("""() => {
            const day = state.days[state.activeDay];
            const task = day.tasks[0];
            addSubtask(state.activeDay, task.id);
        }""")
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "03-add-subtask", "Add subtask input")

        # Re-inject mock data (addSubtask adds an empty child; reset cleanly)
        page.evaluate(MOCK_JS)
        page.evaluate("() => new Promise(r => setTimeout(r, 200))")

        # ── 4. Status badges (done, partial, important) ───────────────────────
        page.evaluate("""() => {
            const day = state.days[state.activeDay];
            day.tasks[0].status = 'done';
            day.tasks[0].children.forEach(c => c.status = 'done');
            day.tasks[1].status = 'partial';
            day.tasks[3].important = true;
            renderTaskArea();
        }""")
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "04-task-statuses", "Task status badges")

        # Re-inject for clean state
        page.evaluate(MOCK_JS)
        page.evaluate("() => new Promise(r => setTimeout(r, 200))")

        # ── 5. Collapsed state with indicators ───────────────────────────────
        page.evaluate("""() => {
            const day = state.days[state.activeDay];
            const parent = day.tasks.find(t => t.children.length > 0);
            if (parent) {
                parent.children[0].status = 'partial';
                parent.children[1].important = true;
                _collapsed.add(parent.id);
                renderTaskArea();
            }
        }""")
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "05-collapsed-indicators", "Collapsed task with indicators")

        # Re-inject
        page.evaluate(MOCK_JS)
        page.evaluate("() => new Promise(r => setTimeout(r, 200))")

        # ── 6. Recurring task badge ───────────────────────────────────────────
        # "Daily standup prep" is already recurring in mock data
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "06-recurring", "Recurring task badge")

        # ── 7. Context panel ──────────────────────────────────────────────────
        page.evaluate("""() => {
            const day = state.days[state.activeDay];
            const t = day.tasks.find(t => t.context.links.length > 0);
            if (t) openContext(state.activeDay, t.id);
        }""")
        page.evaluate("() => new Promise(r => setTimeout(r, 400))")
        shot(page, "07-context-panel", "Context panel with links")

        page.evaluate("closeContextPanel()")

        # ── 8. Collapse all ───────────────────────────────────────────────────
        page.evaluate("() => { collapseAll(); }")
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "08-collapse-all", "All tasks collapsed")
        page.evaluate("() => { expandAll(); }")

        # ── 9. Move to next day button ────────────────────────────────────────
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "09-move-button", "Move to next day button in header")

        # ── 10. Summary modal ─────────────────────────────────────────────────
        page.evaluate("openSummaryModal()")
        page.evaluate("() => new Promise(r => setTimeout(r, 300))")
        shot(page, "10-summary-empty", "Summary modal")

        # Generate a summary
        page.evaluate("""() => {
            document.getElementById('sum-period').value = 'month';
            generateSummary();
        }""")
        page.evaluate("() => new Promise(r => setTimeout(r, 400))")
        shot(page, "11-summary-result", "Summary output")
        page.evaluate("closeSummaryModal()")

        # ── 11. Month tabs ────────────────────────────────────────────────────
        page.evaluate("() => new Promise(r => setTimeout(r, 200))")
        shot(page, "12-month-tabs", "Month tab navigation")

        browser.close()
        print("\nDone! Screenshots saved to docs/")


if __name__ == "__main__":
    run()
