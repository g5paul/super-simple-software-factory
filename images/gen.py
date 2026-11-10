"""Draws the diagrams 08 to 14 in this folder, in the style of the set before them.

    python3 images/gen.py            # writes each SVG next to this script

Each diagram is a few calls to box, frame, arrow and label. Change the
coordinates or text, run it, then open the SVG to check nothing overlaps or
runs off the edge, since nothing here checks that.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape as x
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
BLUE, AMBER, GREEN, VIOLET, WHITE, RED, SLATE = "#38BDF8", "#F59E0B", "#34D399", "#A78BFA", "#E2E8F0", "#F87171", "#475569"
def head(w, h, title):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="Inter, system-ui, sans-serif">',
    '<defs><style><![CDATA[ .title{font-weight:700;font-size:30px;fill:#FFFFFF;letter-spacing:-0.5px} .mono{font-family:\'JetBrains Mono\',ui-monospace,monospace} .nd{font-size:15px;fill:#FFFFFF} .lbl{font-size:12px;fill:#94A3B8} .hd3{font-weight:700;font-size:13px;fill:#FFFFFF} .foot{font-size:14px;fill:#CBD5E1} .warn{font-size:12px;fill:#FCA5A5} ]]></style>',
    f'<marker id="aw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{WHITE}"/></marker>',
    f'<marker id="ag" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#94A3B8"/></marker></defs>',
    f'<rect x="0" y="0" width="{w}" height="{h}" rx="14" fill="#0B0F14" stroke="#1B2431"/>',
    f'<text class="title" x="{w/2}" y="50" text-anchor="middle">{x(title)}</text>']
def box(x0, y0, w, h, stroke, lines, sw=2, dash=None, fill="#0F172A"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    s = [f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>']
    n = len(lines); top = y0 + h/2 - (n-1)*10 + 5
    for i, (cls, t) in enumerate(lines):
        s.append(f'<text class="{cls}" x="{x0+w/2}" y="{top+i*20}" text-anchor="middle">{x(t)}</text>')
    return s
def frame(x0, y0, w, h, stroke, label):
    return [f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="14" fill="none" stroke="{stroke}" stroke-width="1.6" stroke-dasharray="6 5"/>',
            f'<text class="lbl mono" x="{x0+16}" y="{y0+22}" fill="{stroke}" style="fill:{stroke}">{x(label)}</text>']
def arrow(d, dashed=False, grey=False):
    st = ' stroke-dasharray="5 5"' if dashed else ""
    c, m = ("#94A3B8", "ag") if grey else (WHITE, "aw")
    return [f'<path d="{d}" fill="none" stroke="{c}" stroke-width="1.8"{st} marker-end="url(#{m})"/>']
def label(x0, y0, t, anchor="middle", cls="lbl mono"):
    return [f'<text class="{cls}" x="{x0}" y="{y0}" text-anchor="{anchor}">{x(t)}</text>']
def foot(w, y0, t):
    return [f'<text class="foot" x="{w/2}" y="{y0}" text-anchor="middle">{x(t)}</text>']

# 1. The operator session
s = head(1100, 600, "The operator session")
s += box(40, 110, 170, 70, BLUE, [("nd", "Engineer")])
s += frame(400, 82, 660, 150, VIOLET, "operator session · Claude Code, on the host or in the container")
s += box(425, 120, 180, 80, VIOLET, [("nd", "sssf skill")])
s += box(640, 120, 180, 80, VIOLET, [("nd", "cookbook")])
s += box(855, 120, 180, 80, VIOLET, [("nd", "writes the prompt,"), ("nd", "launches the chain")])
s += arrow("M210,135 H421") + label(305, 108, "/sssf add a") + label(305, 124, "/health endpoint")
s += arrow("M400,170 H214", dashed=True, grey=True) + label(305, 192, "status, cost,") + label(305, 208, "findings, commits")
s += arrow("M605,160 H636") + arrow("M820,160 H851")
s += frame(400, 330, 660, 160, AMBER, "chain · code owns the loop")
s += box(425, 375, 180, 90, AMBER, [("nd", "agent phase"), ("lbl", "own prompt, tools,"), ("lbl", "session")])
s += box(640, 375, 180, 90, GREEN, [("nd", "code phase"), ("lbl", "checks, gates")])
s += box(855, 375, 180, 90, AMBER, [("nd", "agent phase"), ("lbl", "own prompt, tools,"), ("lbl", "session")])
s += arrow("M605,420 H636") + arrow("M820,420 H851")
s += arrow("M945,200 V326") + label(935, 270, "uv run adws/adw_*.py", anchor="end")
s += [f'<ellipse cx="180" cy="380" rx="110" ry="16" fill="#0F172A" stroke="{WHITE}" stroke-width="1.8"/>',
      f'<path d="M70,380 V450 A110,16 0 0 0 290,450 V380" fill="#0F172A" stroke="{WHITE}" stroke-width="1.8"/>',
      f'<ellipse cx="180" cy="380" rx="110" ry="16" fill="#0F172A" stroke="{WHITE}" stroke-width="1.8"/>']
s += label(180, 420, "trace", cls="nd") + label(180, 442, "adws/adw_data/sssf.db")
s += arrow("M400,420 H294") + label(347, 410, "every event")
s += arrow("M180,364 V275 H500 V236", dashed=True, grey=True) + label(190, 300, "the operator reads it", anchor="start")
s += foot(1100, 550, "The operator sees the trace, never an agent's context.")
(OUT / "08_operator.svg").write_text("\n".join(s + ["</svg>"]) + "\n")

# 2. Guards
W = 1220
s = head(W, 560, "Where each guard acts")
s += frame(30, 80, 230, 250, RED, "agentsh wrap · when on")
s += box(60, 160, 170, 80, AMBER, [("nd", "agent"), ("lbl", "Claude Code or pi")])
s += label(400, 92, "before a command or read", cls="hd3")
s += box(300, 105, 200, 95, WHITE, [("nd", "command rules"), ("lbl", "read the command's text"), ("warn", "refused on a match")])
s += box(300, 215, 200, 95, WHITE, [("nd", "secret files"), ("lbl", "the Read tool and env"), ("warn", "refused for a listed file")])
s += box(560, 170, 160, 60, WHITE, [("nd", "the command runs")])
s += label(880, 92, "after each phase", cls="hd3")
s += box(780, 105, 200, 95, GREEN, [("nd", "writes check"), ("lbl", "paths the phase may write"), ("warn", "puts back the rest")])
s += box(780, 215, 200, 95, GREEN, [("nd", "git-directory guard"), ("lbl", "anything under .git"), ("warn", "puts it back, stops the run")])
s += label(1110, 92, "at push", cls="hd3")
s += box(1020, 160, 180, 95, BLUE, [("nd", "pre-push hook"), ("lbl", "scans for secrets"), ("warn", "refuses an agent's push")])
s += arrow("M230,185 H296") + arrow("M230,215 C265,215 265,262 296,262")
s += arrow("M500,152 C530,152 530,190 556,190")
s += arrow("M720,190 C750,190 750,152 776,152") + arrow("M720,210 C750,210 750,262 776,262")
s += arrow("M980,152 C1000,152 1000,195 1016,195") + arrow("M980,262 C1000,262 1000,220 1016,220")
s += arrow("M145,330 V420 H640 V234", dashed=True, grey=True)
s += label(392, 445, "agentsh: each exec, file open and connection, checked at the system call")
s += foot(W, 515, "The text checks read what the agent asks for. agentsh sees what actually runs.")
(OUT / "09_guards.svg").write_text("\n".join(s + ["</svg>"]) + "\n")

# 3. Where a run runs
W = 1200
s = head(W, 640, "Where a run runs")
cols = [
 (BLUE,  "sandbox: none", "local_guard: text, the default", ["agents run as your user", "on this machine"], ["text checks"], ["nothing"], ["checked-out branch,", "or --worktree"]),
 (RED,   "sandbox: none", "local_guard: agentsh", ["agents run under", "agentsh wrap"], ["text checks", "and agentsh"], ["nothing"], ["checked-out branch,", "or --worktree"]),
 (GREEN, "sandbox: openshell", "a microVM on this machine", ["agents, checks and git", "in the VM"], ["text checks"], ["nothing"], ["its own branch", "and worktree"]),
 (AMBER, "sandbox: vercel or e2b", "a VM in the cloud", ["agents, checks and git", "in the VM"], ["text checks"], ["the code and an", "inference-only token"], ["its own branch", "and worktree"]),
]
for i, (c, sb, sub, where, guards, leaves, branch) in enumerate(cols):
    x0 = 40 + i*285; w = 265
    s += [f'<rect x="{x0}" y="90" width="{w}" height="440" rx="12" fill="#0F172A" stroke="{c}" stroke-width="2"/>']
    s += [f'<text class="nd mono" x="{x0+w/2}" y="122" text-anchor="middle" style="fill:{c}">{x(sb)}</text>']
    s += label(x0+w/2, 144, sub)
    s += [f'<line x1="{x0+16}" y1="160" x2="{x0+w-16}" y2="160" stroke="{SLATE}"/>']
    y = 192
    for head_, vals in (("where the agents run", where), ("guards", guards), ("what leaves the machine", leaves), ("branch", branch)):
        s += label(x0+20, y, head_, anchor="start")
        for j, v in enumerate(vals):
            s += [f'<text class="nd" x="{x0+20}" y="{y+24+j*20}">{x(v)}</text>']
        y += 88
s += foot(W, 575, "agentsh needs sandbox: none. Where the operator runs is a separate choice,")
s += foot(W, 597, "and a repo on agentsh needs its operator on the host.")
(OUT / "10_run_modes.svg").write_text("\n".join(s + ["</svg>"]) + "\n")

# 11. Updating a repo to a newer skill
W = 1200
s = head(W, 620, "Updating a repo to a newer skill")
s += box(40, 120, 200, 90, BLUE, [("nd", "the hub's skill"), ("lbl", ".claude/skills/sssf"), ("lbl", "on main")])
s += box(300, 120, 200, 90, VIOLET, [("nd", "just sssf-update"), ("lbl", "on a short branch"), ("lbl", "copies the skill in")])
s += arrow("M240,165 H296") + arrow("M500,165 H556")
s += frame(560, 80, 600, 300, WHITE, "each file, by its stamp.json record")
rows = [("unchanged in the repo", GREEN, "replaced"), ("changed in the repo,", WHITE, "kept"), ("changed in both", AMBER, "flagged !")]
subs = ["", "nothing new in the skill", "take it, or copy the change in and keep it"]
for i, ((t, c, r), sub) in enumerate(zip(rows, subs)):
    y = 120 + i*85
    s += [f'<text class="nd" x="590" y="{y+30}">{x(t)}</text>']
    if sub: s += label(590, y+50, sub, anchor="start", cls="lbl")
    s += box(960, y+8, 180, 50, c, [("nd", r)])
    s += arrow(f"M900,{y+33} H956", grey=True)
s += arrow("M860,380 V405 H185 V436")
s += box(40, 440, 290, 80, WHITE, [("nd", "check again"), ("lbl", "install.py --update, until"), ("lbl", "nothing is left to review")])
s += box(380, 440, 240, 80, GREEN, [("nd", "quality check"), ("lbl", "just quality")])
s += box(670, 440, 240, 80, WHITE, [("nd", "commit"), ("lbl", "then fast-forward main")])
s += box(960, 440, 200, 80, BLUE, [("nd", "just projects"), ("lbl", "in the hub: every repo"), ("lbl", "against the hub")], dash="6 5")
s += arrow("M330,480 H376") + arrow("M620,480 H666") + arrow("M910,480 H956", dashed=True, grey=True)
s += foot(W, 580, "The update never overwrites a file the repo changed. A flagged file waits for your take or keep.")
(OUT / "11_update.svg").write_text("\n".join(s + ["</svg>"]) + "\n")

# 12. The guard-change chain
s = head(W, 620, "The guard-change chain")
row1 = [(40, AMBER, ["planner", "threat model, baseline,", "bar, out of scope"]),
        (320, AMBER, ["spec reviewer", "passes the spec", "before any code"]),
        (600, AMBER, ["builder", "changes the guard"]),
        (880, GREEN, ["verify", "the hub suite and", "the chain's own tests"])]
for x0, c, ls in row1:
    s += box(x0, 100, 240, 95, c, [("nd", ls[0])] + [("lbl", l) for l in ls[1:]])
s += arrow("M280,147 H316") + arrow("M560,147 H596") + arrow("M840,147 H876")
s += box(880, 265, 240, 100, RED, [("nd", "bypass reviewer"), ("lbl", "judges a snapshot commit"), ("lbl", "against the baseline")])
s += arrow("M1000,195 V261")
s += arrow("M880,315 H720 V199", dashed=True, grey=True)
s += label(700, 290, "a bypass, a harm or a new", anchor="end") + label(700, 306, "false positive: the builder", anchor="end") + label(700, 322, "fixes it, at most three rounds", anchor="end")
s += box(880, 440, 240, 95, GREEN, [("nd", "passes"), ("lbl", "commits on the branch;"), ("lbl", "the engineer merges")])
s += box(540, 440, 280, 95, RED, [("nd", "stops"), ("lbl", "after three rounds, or a fix"), ("lbl", "that adds a mechanism")])
s += arrow("M1000,365 V436") + arrow("M960,365 C960,400 680,400 680,436")
s += box(160, 440, 300, 95, WHITE, [("nd", "guard_report.md"), ("lbl", "every finding, for the engineer;"), ("lbl", "a stopped run's code stays in the tree")])
s += arrow("M540,487 H464", dashed=True, grey=True)
s += foot(W, 585, "Nothing merges to main on its own.")
(OUT / "12_guard_chain.svg").write_text("\n".join(s + ["</svg>"]) + "\n")

# 13. A run in a VM
s = head(W, 620, "A run in a VM")
s += frame(40, 85, 420, 440, BLUE, "this machine")
s += box(70, 125, 360, 75, BLUE, [("nd", "the chain, adw_*.py"), ("lbl", "stays here and starts each command in the VM")])
s += box(70, 225, 170, 75, WHITE, [("nd", "worktree"), ("lbl", "sssf/<adw_id>")])
s += box(260, 225, 170, 75, WHITE, [("nd", "trace"), ("lbl", "sssf.db")])
s += box(70, 325, 360, 75, AMBER, [("nd", "the model credential"), ("lbl", "Claude's inference-only token,"), ("lbl", "or pi's ChatGPT token")])
s += label(250, 435, "OBS_AUTH_TOKEN never leaves the machine")
s += label(250, 455, "the patch lands in the run's session folder")
s += frame(740, 85, 420, 440, AMBER, "the VM: Vercel, E2B, or OpenShell")
s += box(770, 125, 360, 75, WHITE, [("nd", "a clone of the run's branch"), ("lbl", "at the worktree's own path")])
s += box(770, 225, 360, 75, AMBER, [("nd", "agents"), ("lbl", "get the model credential, nothing else")])
s += box(770, 325, 360, 75, GREEN, [("nd", "checks and git"), ("lbl", "get no credential")])
s += arrow("M460,162 H736") + label(598, 152, "the branch, cloned in")
s += arrow("M460,345 H736") + label(598, 335, "the credential, to agents only")
s += arrow("M740,245 H464") + label(598, 235, "commits, a git bundle each time")
s += arrow("M740,285 H434", grey=True) + label(598, 305, "events, streamed to the trace")
s += arrow("M740,470 H464", dashed=True, grey=True) + label(598, 460, "at the end: uncommitted work,") + label(598, 492, "saved as a patch, not applied")
s += foot(W, 575, "The VM is deleted at the end of the run. OpenShell keeps the code on this machine; Vercel and E2B upload it.")
(OUT / "13_vm_run.svg").write_text("\n".join(s + ["</svg>"]) + "\n")

# 14. A feature from tickets
s = head(W, 640, "A feature from tickets")
s += box(40, 100, 270, 105, WHITE, [("nd mono", ".scratch/<feature>/"), ("lbl mono", "spec.md"), ("lbl mono", "issues/01-*.md, 02-*.md"), ("lbl", "committed first")])
s += box(370, 100, 260, 105, GREEN, [("nd", "read"), ("lbl", "checks the folder, orders"), ("lbl", "the tickets, refuses before"), ("lbl", "any agent spends")])
s += box(690, 100, 230, 105, AMBER, [("nd", "scout"), ("lbl", "explores once"), ("lbl", "for the whole feature")])
s += arrow("M310,152 H366") + arrow("M630,152 H686")
s += frame(40, 250, 1120, 170, AMBER, "for each ticket, in order")
s += box(80, 290, 280, 80, AMBER, [("nd", "build test-first"), ("lbl", "a fresh builder per ticket")])
s += box(460, 290, 280, 80, GREEN, [("nd", "verify"), ("lbl", "the suite and every quality check")])
s += box(840, 290, 280, 80, GREEN, [("nd", "commit"), ("lbl", "the ticket lands on its own")])
s += arrow("M360,330 H456") + arrow("M740,330 H836")
s += arrow("M980,370 V398 H220 V374", dashed=True, grey=True) + label(600, 412, "next ticket")
s += arrow("M805,205 V250")
s += box(40, 465, 250, 75, AMBER, [("nd", "standards review")])
s += box(330, 465, 250, 75, AMBER, [("nd", "spec review")])
s += box(620, 465, 250, 75, AMBER, [("nd", "documenter")])
s += box(910, 465, 250, 75, BLUE, [("nd", "just merge <adw_id>")])
s += arrow("M165,420 V461") + arrow("M290,502 H326") + arrow("M580,502 H616") + arrow("M870,502 H906", dashed=True, grey=True)
s += foot(W, 595, "A rerun skips finished tickets and resumes at the first unfinished one.")
(OUT / "14_tickets.svg").write_text("\n".join(s + ["</svg>"]) + "\n")
