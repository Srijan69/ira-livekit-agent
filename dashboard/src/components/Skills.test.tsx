import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SkillSummary, SkillsApi, SkillRun } from "@shared/types";
import type { ExecutionActivity } from "@/lib/execution";
import { Skills } from "./Skills";

const ready: SkillSummary = {
  slug: "notes",
  name: "Open Notes",
  description: "Open the notes app",
  state: "enabled",
  version: 2,
  source: "compiled",
  pinned: false,
  archived: false,
  use_count: 1,
  failure_count: 0,
  executable: true,
};
function setupApi(entries: SkillSummary[] = [ready]) {
  let rows = entries;
  const api: SkillsApi = {
    list: async () => ({ skills: rows }),
    history: async () => ({ runs: [] }),
    suggestions: async () => ({ suggestions: [] }),
    detail: async (slug) => ({
      skill: {
        ...rows.find((item) => item.slug === slug)!,
        markdown: "## Steps\nOpen notes",
        versions: [1, 2],
      },
    }),
    pin: async (slug, pinned) => {
      rows = rows.map((row) => (row.slug === slug ? { ...row, pinned } : row));
      return { skill: rows[0] };
    },
    archive: async (slug) => {
      rows = rows.map((row) =>
        row.slug === slug ? { ...row, archived: true } : row,
      );
      return { archived: true };
    },
    restore: async (slug) => {
      rows = rows.map((row) =>
        row.slug === slug ? { ...row, archived: false } : row,
      );
      return { skill: rows[0] };
    },
  };
  return api;
}

const waitingRun = {
  run_id: "notes-run",
  skill_slug: "notes",
  skill_name: "Open Notes",
  skill_version: 2,
  status: "waiting",
  completed_steps: 0,
  total_steps: 2,
  failed_step: null,
  message: "An answer is needed to continue.",
  verified: false,
  started_at: 1700000000000,
  finished_at: null,
  steps: [],
  question: "Which notebook should I open?",
  question_id: "notebook-question",
  input_name: "notebook",
};

const activeRun: ExecutionActivity = {
  id: "activity",
  runId: "session",
  callId: "call",
  toolName: "run_skill",
  title: "Running a skill",
  skillSlug: "notes",
  skillRunId: "notes-run",
  phase: "running",
  startedAt: 1,
  updatedAt: 2,
  endedAt: null,
};

describe("skills page", () => {
  it("requires an explicit enable request for drafts and exposes review suggestions", async () => {
    const api = setupApi([{ ...ready, state: "draft", executable: false }]);
    api.suggestions = async () => ({
      suggestions: [
        {
          slug: "notes",
          action: "review",
          reason: "Inspect checks before reusing this skill.",
        },
      ],
    });
    const sent: string[] = [];
    render(
      <Skills
        api={api}
        connected
        onSend={(text) => sent.push(text)}
        activities={[]}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Details for Open Notes" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Enable Open Notes" }),
    );
    expect(sent).toEqual(['Enable the saved skill "notes".']);
    expect(
      await screen.findByText("Inspect checks before reusing this skill."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run Open Notes" }),
    ).toBeDisabled();
  });

  it("loads saved skills, sends run through the agent and shows version details", async () => {
    const sent: string[] = [];
    render(
      <Skills
        api={setupApi()}
        connected
        onSend={(text) => sent.push(text)}
        activities={[]}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    fireEvent.click(
      await screen.findByRole("button", { name: "Run Open Notes" }),
    );
    expect(sent).toEqual(['Run the saved skill "notes".']);
    expect(screen.getByRole("status")).toHaveTextContent("Sent to IRA");
    fireEvent.click(
      screen.getByRole("button", { name: "Details for Open Notes" }),
    );
    expect(await screen.findByText("Versions: 1, 2")).toBeInTheDocument();
    expect(screen.getByText(/## Steps/)).toBeInTheDocument();
  });

  it("blocks drafts and offline runs, and persists pin/archive/restore controls", async () => {
    const { rerender } = render(
      <Skills
        api={setupApi([{ ...ready, state: "draft", executable: false }])}
        connected
        onSend={() => {}}
        activities={[]}
      />,
    );
    expect(
      await screen.findByRole("button", { name: "Run Open Notes" }),
    ).toBeDisabled();
    const api = setupApi();
    rerender(
      <Skills api={api} connected={false} onSend={() => {}} activities={[]} />,
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Run Open Notes" }),
      ).toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Pin Open Notes" }));
    expect(
      await screen.findByRole("button", { name: "Unpin Open Notes" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Archive Open Notes" }));
    expect(
      await screen.findByRole("button", { name: "Restore Open Notes" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore Open Notes" }));
    expect(
      await screen.findByRole("button", { name: "Archive Open Notes" }),
    ).toBeInTheDocument();
  });

  it("keeps bridge errors visible and lets the user retry", async () => {
    const api = setupApi();
    api.list = vi
      .fn()
      .mockResolvedValueOnce({ error: "Bridge unavailable" })
      .mockResolvedValue({ skills: [ready] });
    render(<Skills api={api} connected onSend={() => {}} activities={[]} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Bridge unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh skills" }));
    expect(
      await screen.findByRole("button", { name: "Run Open Notes" }),
    ).toBeEnabled();
  });

  it("shows verified history and stops only the correlated active run", async () => {
    const api = setupApi();
    api.history = async () => ({
      runs: [
        {
          run_id: "old-run",
          skill_slug: "notes",
          skill_name: "Open Notes",
          skill_version: 1,
          status: "uncertain",
          completed_steps: 0,
          total_steps: 2,
          verified: false,
          message: "Window could not be observed",
          steps: [],
          started_at: 1700000000000,
          finished_at: 1700000000200,
        } as unknown as SkillRun,
      ],
    });
    const sent: string[] = [];
    render(
      <Skills
        api={api}
        connected
        onSend={(text) => sent.push(text)}
        activities={[
          {
            id: "a",
            runId: "session",
            callId: "call",
            toolName: "run_skill",
            title: "Running a skill",
            skillSlug: "notes",
            skillRunId: "active-run",
            phase: "verifying",
            progress: "Step 2 of 3",
            startedAt: 1,
            updatedAt: 2,
            endedAt: null,
          },
        ]}
      />,
    );
    expect(
      await screen.findByText("Window could not be observed"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Step 2 of 3/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stop Open Notes" }));
    expect(sent).toEqual(['Stop the skill run "active-run".']);
    expect(
      within(screen.getByLabelText("Run history")).getByText("Uncertain"),
    ).toBeInTheDocument();
  });

  it("answers the selected waiting run with its exact question identity", async () => {
    const api = setupApi();
    api.history = async () => ({
      runs: [
        waitingRun,
        {
          ...waitingRun,
          run_id: 'second-"run',
          question_id: 'second-"question',
          question: "Which folder should I use?",
        },
      ],
    });
    const sent: string[] = [];
    render(
      <Skills api={api} connected onSend={(text) => sent.push(text)} activities={[]} />,
    );

    const notebook = await screen.findByRole("textbox", { name: waitingRun.question });
    const folder = screen.getByRole("textbox", { name: "Which folder should I use?" });
    expect(within(screen.getByLabelText("Run history")).getAllByText("Waiting for answer")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Run Open Notes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Archive Open Notes" })).toBeDisabled();
    fireEvent.change(notebook, { target: { value: "Personal" } });
    fireEvent.change(folder, { target: { value: 'Work "2026"' } });
    fireEvent.click(within(folder.closest("form")!).getByRole("button", { name: "Answer" }));

    expect(sent).toEqual([
      'Answer skill "second-\\"run" question "second-\\"question" with "Work \\"2026\\"".',
    ]);
    expect(notebook).toHaveValue("Personal");
    expect(screen.getByLabelText("Run history")).not.toHaveTextContent("second-\"run");
    expect(screen.getByLabelText("Run history")).not.toHaveTextContent("notebook-question");
  });

  it("requires a nonempty answer and a connection before sending", async () => {
    const api = setupApi();
    api.history = async () => ({ runs: [waitingRun] });
    const sent: string[] = [];
    const onSend = (text: string) => { sent.push(text); };
    const { rerender } = render(
      <Skills api={api} connected onSend={onSend} activities={[]} />,
    );
    const input = await screen.findByRole("textbox", { name: waitingRun.question });
    const answer = screen.getByRole("button", { name: "Answer" });
    expect(answer).toBeDisabled();
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(input.closest("form")!);
    expect(sent).toEqual([]);
    fireEvent.change(input, { target: { value: "Personal" } });
    expect(answer).toBeEnabled();

    rerender(<Skills api={api} connected={false} onSend={onSend} activities={[]} />);
    expect(answer).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop Open Notes" })).toBeDisabled();
    fireEvent.submit(input.closest("form")!);
    expect(sent).toEqual([]);
    expect(input).toHaveValue("Personal");

    rerender(<Skills api={api} connected onSend={onSend} activities={[]} />);
    fireEvent.click(answer);
    expect(sent).toEqual(['Answer skill "notes-run" question "notebook-question" with "Personal".']);
  });

  it("keeps drafts across fresh history records and blocks duplicate answers", async () => {
    const api = setupApi();
    let refreshCount = 0;
    api.history = async () => ({
      runs: [{ ...waitingRun, message: `Refresh ${++refreshCount}` }],
    });
    const sent: string[] = [];
    render(
      <Skills api={api} connected onSend={(text) => sent.push(text)} activities={[]} />,
    );
    const input = await screen.findByRole("textbox", { name: waitingRun.question });
    fireEvent.change(input, { target: { value: "Personal" } });
    fireEvent.click(screen.getByRole("button", { name: "Refresh skills" }));
    await screen.findByText("Refresh 2");
    expect(input).toHaveValue("Personal");
    fireEvent.submit(input.closest("form")!);
    fireEvent.submit(input.closest("form")!);
    expect(sent).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Answer" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Refresh skills" }));
    await screen.findByText("Refresh 3");
    fireEvent.submit(input.closest("form")!);
    expect(sent).toHaveLength(1);
    expect(input).toHaveValue("Personal");
    expect(screen.getByRole("button", { name: "Stop Open Notes" })).toBeEnabled();
  });

  it("lets the user explicitly correct an answer to the same waiting question", async () => {
    const api = setupApi();
    let current = { ...waitingRun };
    api.history = async () => ({ runs: [{ ...current }] });
    const sent: string[] = [];
    render(
      <Skills api={api} connected onSend={(text) => sent.push(text)} activities={[]} />,
    );
    const input = await screen.findByRole("textbox", { name: waitingRun.question });
    fireEvent.change(input, { target: { value: "Unknown notebook" } });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    expect(input).toBeDisabled();

    current = { ...current, message: "The question is still waiting for an answer." };
    fireEvent.click(screen.getByRole("button", { name: "Refresh skills" }));
    await screen.findByText(current.message);
    expect(screen.getByRole("button", { name: "Answer" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Edit answer" }));
    expect(input).toBeEnabled();
    expect(input).toHaveValue("Unknown notebook");
    expect(sent).toHaveLength(1);

    fireEvent.change(input, { target: { value: "Personal" } });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    fireEvent.submit(input.closest("form")!);
    expect(sent).toEqual([
      'Answer skill "notes-run" question "notebook-question" with "Unknown notebook".',
      'Answer skill "notes-run" question "notebook-question" with "Personal".',
    ]);
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "Answer" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit answer" })).toBeEnabled();
  });

  it("keeps submitted answers locked while disconnected", async () => {
    const api = setupApi();
    api.history = async () => ({ runs: [{ ...waitingRun }] });
    const sent: string[] = [];
    const onSend = (text: string) => { sent.push(text); };
    const { rerender } = render(
      <Skills api={api} connected onSend={onSend} activities={[]} />,
    );
    const input = await screen.findByRole("textbox", { name: waitingRun.question });
    fireEvent.change(input, { target: { value: "Personal" } });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));

    rerender(<Skills api={api} connected={false} onSend={onSend} activities={[]} />);
    const edit = screen.getByRole("button", { name: "Edit answer" });
    expect(edit).toBeDisabled();
    fireEvent.click(edit);
    fireEvent.submit(input.closest("form")!);
    expect(input).toBeDisabled();
    expect(sent).toHaveLength(1);

    rerender(<Skills api={api} connected onSend={onSend} activities={[]} />);
    fireEvent.click(edit);
    expect(input).toBeEnabled();
    expect(sent).toHaveLength(1);
  });

  it("clears the old answer and allows an answer to a new question", async () => {
    const api = setupApi();
    let current = { ...waitingRun };
    api.history = async () => ({ runs: [{ ...current }] });
    const sent: string[] = [];
    render(
      <Skills api={api} connected onSend={(text) => sent.push(text)} activities={[]} />,
    );
    const first = await screen.findByRole("textbox", { name: waitingRun.question });
    fireEvent.change(first, { target: { value: "Personal" } });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));

    current = { ...current, question_id: "page-question", question: "Which page?", input_name: "page" };
    fireEvent.click(screen.getByRole("button", { name: "Refresh skills" }));
    const next = await screen.findByRole("textbox", { name: "Which page?" });
    expect(next).toHaveValue("");
    expect(screen.getByRole("button", { name: "Answer" })).toBeDisabled();
    fireEvent.change(next, { target: { value: "Shopping" } });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    expect(sent).toEqual([
      'Answer skill "notes-run" question "notebook-question" with "Personal".',
      'Answer skill "notes-run" question "page-question" with "Shopping".',
    ]);
  });

  it("stops the waiting run from history even when active telemetry is absent", async () => {
    const api = setupApi();
    api.history = async () => ({ runs: [waitingRun] });
    const sent: string[] = [];
    render(
      <Skills api={api} connected onSend={(text) => sent.push(text)} activities={[]} />,
    );
    const history = within(screen.getByLabelText("Run history"));
    fireEvent.click(await history.findByRole("button", { name: "Stop Open Notes" }));
    expect(sent).toEqual(['Stop the skill run "notes-run".']);
  });

  it("uses waiting history instead of stale running telemetry for the same run", async () => {
    const api = setupApi();
    api.history = async () => ({ runs: [waitingRun] });
    render(<Skills api={api} connected onSend={() => {}} activities={[activeRun]} />);
    await screen.findByRole("textbox", { name: waitingRun.question });
    expect(screen.getByRole("button", { name: "Run Open Notes" })).toBeDisabled();
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Stop Open Notes" })).toHaveLength(1);
  });

  it("keeps Stop available for another active run of a waiting skill", async () => {
    const api = setupApi();
    api.history = async () => ({ runs: [waitingRun] });
    const sent: string[] = [];
    render(
      <Skills api={api} connected onSend={(text) => sent.push(text)}
        activities={[{ ...activeRun, skillRunId: "other-run" }]} />,
    );
    await screen.findByRole("textbox", { name: waitingRun.question });
    const catalog = within(screen.getByRole("article"));
    fireEvent.click(catalog.getByRole("button", { name: "Stop Open Notes" }));
    expect(sent).toEqual(['Stop the skill run "other-run".']);
    expect(within(screen.getByLabelText("Run history")).getByRole("button", { name: "Stop Open Notes" })).toBeEnabled();
  });

  it.each(["done", "error", "cancelled", "uncertain"])("removes waiting controls when the run becomes %s", async (status) => {
    const api = setupApi();
    let current: SkillRun = { ...waitingRun };
    api.history = async () => ({ runs: [current] });
    render(<Skills api={api} connected onSend={() => {}} activities={[]} />);
    const input = await screen.findByRole("textbox", { name: waitingRun.question });
    fireEvent.change(input, { target: { value: "Personal" } });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    expect(screen.getByRole("button", { name: "Edit answer" })).toBeEnabled();
    current = { ...current, status, finished_at: 1700000000200 };
    fireEvent.click(screen.getByRole("button", { name: "Refresh skills" }));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Answer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit answer" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Open Notes" })).toBeEnabled();
  });

  it.each([undefined, "", "   "])("does not answer a missing or blank question identity (%s)", async (question_id) => {
    const api = setupApi();
    api.history = async () => ({ runs: [{ ...waitingRun, question_id }] });
    const sent: string[] = [];
    render(
      <Skills api={api} connected onSend={(text) => sent.push(text)} activities={[]} />,
    );
    const input = await screen.findByRole("textbox", { name: waitingRun.question });
    fireEvent.change(input, { target: { value: "Personal" } });
    expect(screen.getByRole("button", { name: "Answer" })).toBeDisabled();
    fireEvent.submit(input.closest("form")!);
    expect(sent).toEqual([]);
    expect(screen.getByRole("button", { name: "Stop Open Notes" })).toBeEnabled();
  });
});
