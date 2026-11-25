/**
 * Subagent Widget — /sub, /subclear, /subrm, /subcont commands with stacking live widgets
 *
 * Each /sub spawns a background Pi subagent with its own persistent session,
 * enabling conversation continuations via /subcont.
 *
 * Usage: pi -e extensions/subagent-widget.ts
 * Then:
 *   /sub list files and summarize          — spawn using the parent model/thinking
 *   /sub --model openai/gpt-5 --thinking high review this code
 *   /subcont 1 --thinking xhigh now write tests for it
 *   /subrm 2                               — remove subagent #2 widget
 *   /subclear                              — clear all subagent widgets
 *
 * Under SSSF (SSSF_ADW_ID or SSSF_REFUSE_EXTENSION set when this loads):
 * - each child loads SSSF's refuse extension (SSSF_REFUSE_EXTENSION), so its
 *   `refuse` rules and secret-file check apply to the child's tools;
 * - the child's tool_execution_start/_end events are forwarded to this pi's
 *   stdout, where SSSF's stream check reads them (kill rules stop the run);
 * - subagent_create and subagent_continue wait for the child and return its
 *   result, so no child outlives its parent's call;
 * - the child's session goes in $SSSF_SESSION_DIR/subagents/;
 * - a child never starts when the extension or session dir is missing, or
 *   when its task starts with "@" (pi would attach that file).
 * In both modes the child starts with --no-prompt-templates and --no-themes, so a
 * task starting with "/" loads no prompt template ("/skill:<name>" still works),
 * and with JITI_FS_CACHE=false, so it reads no compiled copy from /tmp/jiti.
 * Without the SSSF settings (an interactive pi) nothing else changes.
 */

import { StringEnum, type ThinkingLevel } from "@mariozechner/pi-ai";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { DynamicBorder } from "@mariozechner/pi-coding-agent";
import { Container, Text } from "@mariozechner/pi-tui";
import { Type } from "@sinclair/typebox";
import { spawn } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { applyExtensionDefaults } from "./themeMap.ts";

const FALLBACK_MODEL = "openrouter/google/gemini-3.5-flash";
const THINKING_OVERRIDES = ["low", "medium", "high", "xhigh"] as const;
type ThinkingOverride = (typeof THINKING_OVERRIDES)[number];

interface SpawnOptions {
	model?: string;
	thinking?: ThinkingOverride;
}

interface SubState {
	id: number;
	status: "running" | "done" | "error";
	task: string;
	textChunks: string[];
	toolCount: number;
	elapsed: number;
	sessionFile: string;   // persistent JSONL session path — used by /subcont to resume
	turnCount: number;     // increments each time /subcont continues this agent
	model: string;
	thinking: ThinkingLevel;
	proc?: any;            // active ChildProcess ref (for kill on /subrm)
}

interface ParsedCommand {
	options: SpawnOptions;
	rest: string;
	error?: string;
}

function readCommandValue(input: string): { value?: string; rest: string } {
	const trimmed = input.trimStart();
	if (!trimmed) return { rest: "" };

	const quote = trimmed[0];
	if (quote === '"' || quote === "'") {
		const end = trimmed.indexOf(quote, 1);
		if (end === -1) return { rest: trimmed };
		return { value: trimmed.slice(1, end), rest: trimmed.slice(end + 1) };
	}

	const end = trimmed.search(/\s/);
	return end === -1
		? { value: trimmed, rest: "" }
		: { value: trimmed.slice(0, end), rest: trimmed.slice(end) };
}

function parseCommandOptions(input: string): ParsedCommand {
	const options: SpawnOptions = {};
	let rest = input.trimStart();

	while (rest.startsWith("--")) {
		const flagMatch = rest.match(/^--(model|thinking)(?:=([^\s]+))?(?:\s+|$)/);
		if (!flagMatch) {
			const flag = rest.match(/^\S+/)?.[0] || rest;
			return { options, rest: "", error: `Unknown or malformed option: ${flag}` };
		}

		const flag = flagMatch[1];
		let value = flagMatch[2];
		rest = rest.slice(flagMatch[0].length);
		if (!value) {
			const parsed = readCommandValue(rest);
			value = parsed.value;
			rest = parsed.rest;
		}
		if (!value) return { options, rest: "", error: `Missing value for --${flag}` };

		if (flag === "model") {
			options.model = value;
			rest = rest.trimStart();
			continue;
		}

		const thinking = value.toLowerCase();
		if (!THINKING_OVERRIDES.includes(thinking as ThinkingOverride)) {
			return {
				options,
				rest: "",
				error: "Thinking must be one of: low, medium, high, xhigh",
			};
		}
		options.thinking = thinking as ThinkingOverride;
		rest = rest.trimStart();
	}

	return { options, rest: rest.trim() };
}

export default function (pi: ExtensionAPI) {
	const agents: Map<number, SubState> = new Map();
	let nextId = 1;
	let widgetCtx: any;

	// Read once, at load, before the model's first tool call. The model's own
	// commands run in child shells and can't change this process's environment.
	const REFUSE_EXTENSION = process.env.SSSF_REFUSE_EXTENSION || "";
	const SESSION_DIR = process.env.SSSF_SESSION_DIR || "";
	const UNDER_SSSF = !!(process.env.SSSF_ADW_ID || REFUSE_EXTENSION);

	// The reason no child may start, or "" when one may.
	function preflight(prompt: string): string {
		if (!UNDER_SSSF) return "";
		if (!REFUSE_EXTENSION || !path.isAbsolute(REFUSE_EXTENSION)) {
			return "SSSF_REFUSE_EXTENSION is unset or not an absolute path, so a subagent would run without SSSF's checks.";
		}
		try {
			if (!fs.statSync(REFUSE_EXTENSION).isFile()) throw new Error("not a file");
		} catch {
			return `SSSF_REFUSE_EXTENSION (${REFUSE_EXTENSION}) is not a regular file, so a subagent would run without SSSF's checks.`;
		}
		if (!SESSION_DIR || !path.isAbsolute(SESSION_DIR)) {
			return "SSSF_SESSION_DIR is unset or not an absolute path, so there is no place for the subagent's session.";
		}
		if (prompt.trimStart().startsWith("@")) {
			return "A subagent task starting with @ is refused: pi would attach that file as an argument.";
		}
		return "";
	}

	// Fail closed: a child SSSF can't see doesn't keep running.
	function killAll(signal: string = "SIGKILL") {
		for (const state of agents.values()) {
			if (state.proc && state.status === "running") {
				try { state.proc.kill(signal); } catch {}
				state.status = "error";
			}
		}
	}

	function forward(state: SubState, event: any) {
		if (!UNDER_SSSF) return;
		const line = JSON.stringify({
			...event,
			toolCallId: `sub${state.id}:${event.toolCallId}`,
			subagent: state.id,
		}) + "\n";
		try {
			// The stream's own write, not the one pi's output guard replaced.
			Object.getPrototypeOf(process.stdout).write.call(process.stdout, line, (err: any) => {
				if (err) killAll();
			});
		} catch {
			killAll();
		}
	}

	if (UNDER_SSSF) {
		process.stdout.on("error", () => killAll());
		process.on("exit", () => killAll("SIGKILL"));
	}

	// ── Session file helpers ──────────────────────────────────────────────────

	function makeSessionFile(id: number): string {
		const dir = UNDER_SSSF
			? path.join(SESSION_DIR, "subagents")
			: path.join(os.homedir(), ".pi", "agent", "sessions", "subagents");
		fs.mkdirSync(dir, { recursive: true });
		return path.join(dir, `subagent-${id}-${Date.now()}.jsonl`);
	}

	// ── Widget rendering ──────────────────────────────────────────────────────

	function updateWidgets() {
		if (!widgetCtx) return;

		for (const [id, state] of Array.from(agents.entries())) {
			const key = `sub-${id}`;
			widgetCtx.ui.setWidget(key, (_tui: any, theme: any) => {
				const container = new Container();
				const borderFn = (s: string) => theme.fg("dim", s);

				container.addChild(new Text("", 0, 0)); // top margin
				container.addChild(new DynamicBorder(borderFn));
				const content = new Text("", 1, 0);
				container.addChild(content);
				container.addChild(new DynamicBorder(borderFn));

				return {
					render(width: number): string[] {
						const lines: string[] = [];
						const statusColor = state.status === "running" ? "accent"
							: state.status === "done" ? "success" : "error";
						const statusIcon = state.status === "running" ? "●"
							: state.status === "done" ? "✓" : "✗";

						const taskPreview = state.task.length > 40
							? state.task.slice(0, 37) + "..."
							: state.task;

						const turnLabel = state.turnCount > 1
							? theme.fg("dim", ` · Turn ${state.turnCount}`)
							: "";

						lines.push(
							theme.fg(statusColor, `${statusIcon} Subagent #${state.id}`) +
							turnLabel +
							theme.fg("dim", `  ${taskPreview}`) +
							theme.fg("dim", `  (${Math.round(state.elapsed / 1000)}s)`) +
							theme.fg("dim", ` | Tools: ${state.toolCount}`)
						);

						const fullText = state.textChunks.join("");
						const lastLine = fullText.split("\n").filter((l: string) => l.trim()).pop() || "";
						if (lastLine) {
							const trimmed = lastLine.length > width - 10
								? lastLine.slice(0, width - 13) + "..."
								: lastLine;
							lines.push(theme.fg("muted", `  ${trimmed}`));
						}

						content.setText(lines.join("\n"));
						return container.render(width);
					},
					invalidate() {
						container.invalidate();
					},
				};
			});
		}
	}

	// ── Streaming helpers ─────────────────────────────────────────────────────

	function processLine(state: SubState, line: string) {
		if (!line.trim()) return;
		try {
			const event = JSON.parse(line);
			const type = event.type;

			if (type === "message_update") {
				const delta = event.assistantMessageEvent;
				if (delta?.type === "text_delta") {
					state.textChunks.push(delta.delta || "");
					updateWidgets();
				}
			} else if (type === "tool_execution_start") {
				state.toolCount++;
				updateWidgets();
				forward(state, event);
			} else if (type === "tool_execution_end") {
				forward(state, event);
			}
		} catch {}
	}

	function resultText(state: SubState, prompt: string): string {
		const result = state.textChunks.join("");
		return `Subagent #${state.id}${state.turnCount > 1 ? ` (Turn ${state.turnCount})` : ""} finished "${prompt}" in ${Math.round(state.elapsed / 1000)}s.\n\nResult:\n${result.slice(0, 8000)}${result.length > 8000 ? "\n\n... [truncated]" : ""}`;
	}

	function spawnAgent(
		state: SubState,
		prompt: string,
		ctx: any,
		options: SpawnOptions = {},
	): Promise<void> {
		const parentProvider = ctx.model?.provider?.trim();
		const parentModelId = ctx.model?.id?.trim();
		const hasParentModel = parentProvider && parentModelId
			&& parentProvider !== "unknown" && parentModelId !== "unknown";
		const parentModel = hasParentModel
			? `${parentProvider}/${parentModelId}`
			: FALLBACK_MODEL;
		const model = options.model?.trim() || parentModel;
		const thinking = options.thinking || pi.getThinkingLevel();
		state.model = model;
		state.thinking = thinking;

		return new Promise<void>((resolve) => {
			const proc = spawn("pi", [
				"--mode", "json",
				"-p",
				"--session", state.sessionFile,   // persistent session for /subcont resumption
				"--no-extensions",
				"--no-prompt-templates", "--no-themes",
				"--model", model,
				"--tools", "read,bash,grep,find,ls",
				"--thinking", thinking,
				// Under SSSF the refuse extension is the child's only extension.
				...(UNDER_SSSF ? ["-e", REFUSE_EXTENSION, "--"] : []),
				prompt,
			], {
				stdio: ["ignore", "pipe", "pipe"],
				// jiti then reads no cached copy of the child's extension.
				env: { ...process.env, JITI_FS_CACHE: "false" },
			});

			state.proc = proc;

			const startTime = Date.now();
			const timer = setInterval(() => {
				state.elapsed = Date.now() - startTime;
				updateWidgets();
			}, 1000);

			let buffer = "";

			proc.stdout!.setEncoding("utf-8");
			proc.stdout!.on("data", (chunk: string) => {
				buffer += chunk;
				const lines = buffer.split("\n");
				buffer = lines.pop() || "";
				for (const line of lines) processLine(state, line);
			});

			proc.stderr!.setEncoding("utf-8");
			proc.stderr!.on("data", (chunk: string) => {
				if (chunk.trim()) {
					state.textChunks.push(chunk);
					updateWidgets();
				}
			});

			proc.on("close", (code) => {
				if (buffer.trim()) processLine(state, buffer);
				clearInterval(timer);
				state.elapsed = Date.now() - startTime;
				state.status = code === 0 ? "done" : "error";
				state.proc = undefined;
				updateWidgets();

				ctx.ui.notify(
					`Subagent #${state.id} ${state.status} in ${Math.round(state.elapsed / 1000)}s`,
					state.status === "done" ? "success" : "error"
				);

				// Under SSSF the tool returns this text itself.
				if (!UNDER_SSSF) {
					pi.sendMessage({
						customType: "subagent-result",
						content: resultText(state, prompt),
						display: true,
					}, { deliverAs: "followUp", triggerTurn: true });
				}

				resolve();
			});

			proc.on("error", (err) => {
				clearInterval(timer);
				state.status = "error";
				state.proc = undefined;
				state.textChunks.push(`Error: ${err.message}`);
				updateWidgets();
				resolve();
			});
		});
	}

	// ── Tools for the Main Agent ──────────────────────────────────────────────

	pi.registerTool({
		name: "subagent_create",
		description: "Spawn a background subagent. Thinking level is required and is the primary way to match the subagent to task complexity: low for lightweight/simple tasks, medium for routine tasks needing moderate reasoning, high for complex multi-step work, and xhigh for the hardest tasks or when accuracy and performance are critical. Unless the user explicitly requests a specific model, omit model and use the default inherited parent model. " + (UNDER_SSSF
				? "Waits for the subagent to finish and returns its result."
				: "Returns immediately and delivers results as a follow-up message."),
		parameters: Type.Object({
			task: Type.String({ description: "The complete task description for the subagent to perform" }),
			model: Type.Optional(Type.String({
				description: "Leave blank or omit unless the user explicitly requests a specific model. Do not choose a different model autonomously. When explicitly requested, provide the override in provider/model form. The default reuses the parent caller's current model and falls back to openrouter/google/gemini-3.5-flash only if the parent has no model.",
			})),
			thinking: StringEnum([...THINKING_OVERRIDES], {
				description: "Required thinking level. Use low for lightweight/simple tasks; medium for routine tasks needing moderate reasoning; high for complex, multi-step, or ambiguous work; and xhigh for the hardest tasks or when accuracy and performance are critical. Pi may clamp the value to the selected model's supported maximum.",
			}),
		}),
		execute: async (callId, args, _signal, _onUpdate, ctx) => {
			widgetCtx = ctx;
			const refused = preflight(args.task);
			if (refused) {
				return { content: [{ type: "text", text: `Error: no subagent started. ${refused}` }] };
			}
			const id = nextId++;
			const state: SubState = {
				id,
				status: "running",
				task: args.task,
				textChunks: [],
				toolCount: 0,
				elapsed: 0,
				sessionFile: makeSessionFile(id),
				turnCount: 1,
				model: "",
				thinking: pi.getThinkingLevel(),
			};
			agents.set(id, state);
			updateWidgets();

			if (UNDER_SSSF) {
				// Wait, so no child outlives this call.
				await spawnAgent(state, args.task, ctx, { model: args.model, thinking: args.thinking });
				return { content: [{ type: "text", text: resultText(state, args.task) }] };
			}

			// Fire-and-forget
			spawnAgent(state, args.task, ctx, { model: args.model, thinking: args.thinking });

			return {
				content: [{ type: "text", text: `Subagent #${id} spawned with ${state.model} (${state.thinking} thinking) and is running in background.` }],
			};
		},
	});

	pi.registerTool({
		name: "subagent_continue",
		description: "Continue an existing subagent conversation. Thinking level is required and is the primary way to match this turn to task complexity: low for lightweight/simple tasks, medium for routine tasks needing moderate reasoning, high for complex multi-step work, and xhigh for the hardest tasks or when accuracy and performance are critical. Unless the user explicitly requests a specific model, omit model and use the default inherited parent model. " + (UNDER_SSSF
				? "Waits for the subagent to finish and returns its result."
				: "Returns immediately while it runs in the background."),
		parameters: Type.Object({
			id: Type.Number({ description: "The ID of the subagent to continue" }),
			prompt: Type.String({ description: "The follow-up prompt or new instructions" }),
			model: Type.Optional(Type.String({
				description: "Leave blank or omit unless the user explicitly requests a specific model. Do not choose a different model autonomously. When explicitly requested, provide the override in provider/model form for this turn. The default reuses the parent caller's current model.",
			})),
			thinking: StringEnum([...THINKING_OVERRIDES], {
				description: "Required thinking level for this turn. Use low for lightweight/simple tasks; medium for routine tasks needing moderate reasoning; high for complex, multi-step, or ambiguous work; and xhigh for the hardest tasks or when accuracy and performance are critical. Pi may clamp the value to the selected model's supported maximum.",
			}),
		}),
		execute: async (callId, args, _signal, _onUpdate, ctx) => {
			widgetCtx = ctx;
			const state = agents.get(args.id);
			if (!state) {
				return { content: [{ type: "text", text: `Error: No subagent #${args.id} found.` }] };
			}
			if (state.status === "running") {
				return { content: [{ type: "text", text: `Error: Subagent #${args.id} is still running.` }] };
			}
			const refused = preflight(args.prompt);
			if (refused) {
				return { content: [{ type: "text", text: `Error: no subagent started. ${refused}` }] };
			}

			state.status = "running";
			state.task = args.prompt;
			state.textChunks = [];
			state.elapsed = 0;
			state.turnCount++;
			updateWidgets();

			ctx.ui.notify(`Continuing Subagent #${args.id} (Turn ${state.turnCount})…`, "info");
			if (UNDER_SSSF) {
				await spawnAgent(state, args.prompt, ctx, { model: args.model, thinking: args.thinking });
				return { content: [{ type: "text", text: resultText(state, args.prompt) }] };
			}
			spawnAgent(state, args.prompt, ctx, { model: args.model, thinking: args.thinking });

			return {
				content: [{ type: "text", text: `Subagent #${args.id} continuing with ${state.model} (${state.thinking} thinking) in background.` }],
			};
		},
	});

	pi.registerTool({
		name: "subagent_remove",
		description: "Remove a specific subagent. Kills it if it's currently running.",
		parameters: Type.Object({
			id: Type.Number({ description: "The ID of the subagent to remove" }),
		}),
		execute: async (callId, args, _signal, _onUpdate, ctx) => {
			widgetCtx = ctx;
			const state = agents.get(args.id);
			if (!state) {
				return { content: [{ type: "text", text: `Error: No subagent #${args.id} found.` }] };
			}

			if (state.proc && state.status === "running") {
				state.proc.kill("SIGTERM");
			}
			ctx.ui.setWidget(`sub-${args.id}`, undefined);
			agents.delete(args.id);

			return {
				content: [{ type: "text", text: `Subagent #${args.id} removed successfully.` }],
			};
		},
	});

	pi.registerTool({
		name: "subagent_list",
		description: "List all active and finished subagents, showing their IDs, tasks, and status.",
		parameters: Type.Object({}),
		execute: async () => {
			if (agents.size === 0) {
				return { content: [{ type: "text", text: "No active subagents." }] };
			}

			const list = Array.from(agents.values()).map(s =>
				`#${s.id} [${s.status.toUpperCase()}] (Turn ${s.turnCount}, ${s.model}, ${s.thinking}) - ${s.task}`
			).join("\n");

			return {
				content: [{ type: "text", text: `Subagents:\n${list}` }],
			};
		},
	});
	// ── /sub [--model <model>] [--thinking <level>] <task> ────────────────────

	pi.registerCommand("sub", {
		description: "Spawn a subagent: /sub [--model provider/model] [--thinking low|medium|high|xhigh] <task>",
		handler: async (args, ctx) => {
			widgetCtx = ctx;

			const parsed = parseCommandOptions(args || "");
			if (parsed.error) {
				ctx.ui.notify(parsed.error, "error");
				return;
			}
			const task = parsed.rest;
			if (!task) {
				ctx.ui.notify("Usage: /sub [--model provider/model] [--thinking low|medium|high|xhigh] <task>", "error");
				return;
			}
			const refused = preflight(task);
			if (refused) {
				ctx.ui.notify(`No subagent started. ${refused}`, "error");
				return;
			}

			const id = nextId++;
			const state: SubState = {
				id,
				status: "running",
				task,
				textChunks: [],
				toolCount: 0,
				elapsed: 0,
				sessionFile: makeSessionFile(id),
				turnCount: 1,
				model: "",
				thinking: pi.getThinkingLevel(),
			};
			agents.set(id, state);
			updateWidgets();

			// Fire-and-forget
			spawnAgent(state, task, ctx, parsed.options);
			ctx.ui.notify(`Subagent #${id}: ${state.model} (${state.thinking} thinking)`, "info");
		},
	});

	// ── /subcont <id> [--model <model>] [--thinking <level>] <prompt> ─────────

	pi.registerCommand("subcont", {
		description: "Continue a subagent: /subcont <id> [--model provider/model] [--thinking low|medium|high|xhigh] <prompt>",
		handler: async (args, ctx) => {
			widgetCtx = ctx;

			const trimmed = args?.trim() ?? "";
			const idMatch = trimmed.match(/^(\d+)(?:\s+|$)/);
			if (!idMatch) {
				ctx.ui.notify("Usage: /subcont <id> [--model provider/model] [--thinking low|medium|high|xhigh] <prompt>", "error");
				return;
			}

			const num = parseInt(idMatch[1], 10);
			const parsed = parseCommandOptions(trimmed.slice(idMatch[0].length));
			if (parsed.error) {
				ctx.ui.notify(parsed.error, "error");
				return;
			}
			const prompt = parsed.rest;

			if (!prompt) {
				ctx.ui.notify("Usage: /subcont <id> [--model provider/model] [--thinking low|medium|high|xhigh] <prompt>", "error");
				return;
			}

			const state = agents.get(num);
			if (!state) {
				ctx.ui.notify(`No subagent #${num} found. Use /sub to create one.`, "error");
				return;
			}

			if (state.status === "running") {
				ctx.ui.notify(`Subagent #${num} is still running — wait for it to finish first.`, "warning");
				return;
			}
			const refused = preflight(prompt);
			if (refused) {
				ctx.ui.notify(`No subagent started. ${refused}`, "error");
				return;
			}

			// Resume: update state for a new turn
			state.status = "running";
			state.task = prompt;
			state.textChunks = [];
			state.elapsed = 0;
			state.turnCount++;
			updateWidgets();

			ctx.ui.notify(`Continuing Subagent #${num} (Turn ${state.turnCount})…`, "info");

			// Fire-and-forget — reuses the same sessionFile for conversation history
			spawnAgent(state, prompt, ctx, parsed.options);
			ctx.ui.notify(`Subagent #${num}: ${state.model} (${state.thinking} thinking)`, "info");
		},
	});

	// ── /subrm <number> ───────────────────────────────────────────────────────

	pi.registerCommand("subrm", {
		description: "Remove a specific subagent widget: /subrm <number>",
		handler: async (args, ctx) => {
			widgetCtx = ctx;

			const num = parseInt(args?.trim() ?? "", 10);
			if (isNaN(num)) {
				ctx.ui.notify("Usage: /subrm <number>", "error");
				return;
			}

			const state = agents.get(num);
			if (!state) {
				ctx.ui.notify(`No subagent #${num} found.`, "error");
				return;
			}

			// Kill the process if still running
			if (state.proc && state.status === "running") {
				state.proc.kill("SIGTERM");
				ctx.ui.notify(`Subagent #${num} killed and removed.`, "warning");
			} else {
				ctx.ui.notify(`Subagent #${num} removed.`, "info");
			}

			ctx.ui.setWidget(`sub-${num}`, undefined);
			agents.delete(num);
		},
	});

	// ── /subclear ─────────────────────────────────────────────────────────────

	pi.registerCommand("subclear", {
		description: "Clear all subagent widgets",
		handler: async (_args, ctx) => {
			widgetCtx = ctx;

			let killed = 0;
			for (const [id, state] of Array.from(agents.entries())) {
				if (state.proc && state.status === "running") {
					state.proc.kill("SIGTERM");
					killed++;
				}
				ctx.ui.setWidget(`sub-${id}`, undefined);
			}

			const total = agents.size;
			agents.clear();
			nextId = 1;

			const msg = total === 0
				? "No subagents to clear."
				: `Cleared ${total} subagent${total !== 1 ? "s" : ""}${killed > 0 ? ` (${killed} killed)` : ""}.`;
			ctx.ui.notify(msg, total === 0 ? "info" : "success");
		},
	});

	// ── Session lifecycle ─────────────────────────────────────────────────────

	pi.on("session_start", async (_event, ctx) => {
		applyExtensionDefaults(import.meta.url, ctx);
		for (const [id, state] of Array.from(agents.entries())) {
			if (state.proc && state.status === "running") {
				state.proc.kill("SIGTERM");
			}
			ctx.ui.setWidget(`sub-${id}`, undefined);
		}
		agents.clear();
		nextId = 1;
		widgetCtx = ctx;
	});
}
