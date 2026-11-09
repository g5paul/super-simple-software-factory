#!/bin/bash
# Run by deny-test.sh under the wrap. Tests the default-deny policy against files with
# ordinary names, outside the allow list, because a name match can't show that the tracer
# found the real file. It touches fake files in $D, and opens real credentials without
# printing them.
D=$HOME/.local/share/agentsh-harvest/secret-probe
r() { printf '%-34s ' "$1"; o=$(bash -c "$2" 2>&1); if printf '%s' "$o" | grep -q probe-value; then echo "READ THE VALUE"; else echo "held  $(printf '%s' "$o" | head -1 | cut -c1-48)"; fi; }
o() { printf '%-34s ' "$1"; if bash -c "$2" >/dev/null 2>&1; then echo "OPENED"; else echo "held"; fi; }
w() { printf '%-34s ' "$1"; if bash -c "$2" >/dev/null 2>&1; then echo "DID IT"; else echo "held"; fi; }
echo "--- read a plain file outside the list"
r "direct"                  "cat $D/plain.txt"
r "dotdot"                  "cat $D/../secret-probe/plain.txt"
r "/proc/self/root"         "cat /proc/self/root$D/plain.txt"
r "/proc/self/cwd"          "cd $D 2>/dev/null; cat /proc/self/cwd/plain.txt"
r "/proc/self/fd/N"         "exec 3<$D; cat /proc/self/fd/3/plain.txt"
r "/proc/thread-self/fd/N"  "exec 3<$D; cat /proc/thread-self/fd/3/plain.txt"
r "/proc/PID/root"          "cat /proc/\$\$/root$D/plain.txt"
r "root fd then path"       "exec 3</; cat /proc/self/fd/3${D}/plain.txt"
r "python dir_fd from /"    "python3 -c 'import os;d=os.open(\"/\",os.O_RDONLY);print(os.read(os.open(\"${D#/}/plain.txt\",os.O_RDONLY,dir_fd=d),99))'"
r "symlink in workspace"    "ln -sfn $D/plain.txt \$PWD/sl; cat \$PWD/sl; rm -f \$PWD/sl"
r "symlinked dir"           "ln -sfn $D \$PWD/sd; cat \$PWD/sd/plain.txt; rm -f \$PWD/sd"
r "hard link in workspace"  "ln -f $D/plain.txt \$PWD/hl; cat \$PWD/hl; rm -f \$PWD/hl"
r "copy"                    "cp $D/plain.txt \$PWD/cp1; cat \$PWD/cp1; rm -f \$PWD/cp1"
r "/proc/self/fd/N .env"    "exec 3<$D; cat /proc/self/fd/3/.env"
r "link/.. plain"           "cat \$PWD/.outlink/../plain.txt"
r "link/.. .env"            "cat \$PWD/.outlink/../.env"
echo "--- change things outside the list"
w "create direct"           "echo x > $D/made-direct"
w "create via root"         "echo x > /proc/self/root$D/made-root"
w "create via root fd"      "exec 3</; echo x > /proc/self/fd/3$D/made-fd"
w "mkdir via root fd"       "exec 3</; mkdir /proc/self/fd/3$D/made-dir"
w "mkdir via /proc/self/cwd" "cd / ; mkdir /proc/self/cwd${D}/made-cwd-dir"
w "delete direct"           "rm $D/del-direct"
w "delete via root"         "rm /proc/self/root$D/del-root"
w "delete via root fd"      "exec 3</; rm /proc/self/fd/3$D/del-fd"
w "delete via /proc/self/cwd" "cd / ; rm /proc/self/cwd${D}/del-cwd"
w "delete via link/.."      "rm \$PWD/.outlink/../del-dd"
w "create via link/.."      "echo x > \$PWD/.outlink/../made-dd"
w "rename via root fd"      "exec 3</; mv /proc/self/fd/3$D/mv-fd /proc/self/fd/3$D/mv-fd-done"
w "rename out to workspace" "mv $D/mv-out \$PWD/mv-out-done"
w "chmod via root fd"       "exec 3</; chmod 600 /proc/self/fd/3$D/chmod-fd"
w "symlink via root fd"     "exec 3</; ln -s x /proc/self/fd/3$D/made-sl"
w "append to ~/.bashrc"     ": >> \$HOME/.bashrc"
w "append to ~/.profile"    ": >> \$HOME/.profile"
w "append to .claude settings" ": >> \$HOME/.claude/settings.json"
w "add a git hook"          "G=\$(git rev-parse --git-common-dir); echo x > \$G/hooks/post-commit"
w "append to git config"    "G=\$(git rev-parse --git-common-dir); : >> \$G/config"
w "git config set"          "git config dd.test 1"
w "append via root fd"      "exec 3</; : >> /proc/self/fd/3\$HOME/.claude/settings.json"
echo "--- open, nothing printed"
o "another repo"            ": < \$HOME/github/agentsh/README.md"
o "another repo's checkout git" ": < \$HOME/github/agentsh/.git/config"
o "~/.ssh/config"           ": < \$HOME/.ssh/config"
o "gh token"                ": < \$HOME/.config/gh/hosts.yml"
o "sssf-sandbox token"      ": < \$HOME/.config/sssf-sandbox/token.env"
o "~/.claude/.credentials.json" ": < \$HOME/.claude/.credentials.json"
o "~/.pi/agent/auth.json"   ": < \$HOME/.pi/agent/auth.json"
o "PID 1 environ"           ": < /proc/1/environ"
o "parent environ"          ": < /proc/\$PPID/environ"
o "self environ"            ": < /proc/self/environ"
o "Windows drive"           ": < /mnt/c/Windows/win.ini"
# Exit 126 is the policy. Any other failure would be the network, so it counts as DID IT.
w "denied after a 100 KB arg" 'git -c a=$(head -c 100000 /dev/zero | tr "\0" x) push --dry-run origin HEAD; [ $? -ne 126 ]'

echo "--- must work, each line should say DID IT"
w "bash -c with 100 KB"     '/bin/bash -c "#$(head -c 100000 /dev/zero | tr "\0" x)"'
w "echo a 120 KB argument"  '/bin/echo "$(head -c 120000 /dev/zero | tr "\0" x)" >/dev/null'
w "2 thousand arguments"    '/bin/echo $(yes a | head -2000) >/dev/null'
w "6 KB commit message"     'git commit -q --allow-empty -m "$(head -c 6000 /dev/zero | tr "\0" x)" && git reset -q --soft HEAD~1'
w "exec with an empty env"  'env -i /bin/true'
w "write to /dev/stdout"    "echo x > /dev/stdout"
w "write to a piped stdout" "(echo x > /dev/stdout) | cat"
w "write /proc/self/fd/1"   "(echo x > /proc/self/fd/1) | cat"
w "create via /proc/self/cwd" "echo x > /proc/self/cwd/.dd-a; rm /proc/self/cwd/.dd-a"
w "create via workspace fd" "exec 3<\$PWD; echo x > /proc/self/fd/3/.dd-b; rm /proc/self/fd/3/.dd-b"
w "rename via workspace fd" "exec 3<\$PWD; : > .dd-c; mv /proc/self/fd/3/.dd-c /proc/self/fd/3/.dd-d; rm .dd-d"
w "mkdir via workspace fd"  "exec 3<\$PWD; mkdir /proc/self/fd/3/.dd-e; rmdir .dd-e"
w "read a deleted file's fd" "echo x > .dd-f; exec 3<.dd-f; rm .dd-f; cat /proc/self/fd/3"
w "uv finds its config"     "uv pip list -q"
w "commit in the worktree"  "git commit -q --allow-empty -m dd && git reset -q --soft HEAD~1"
