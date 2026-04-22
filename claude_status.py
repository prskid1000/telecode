import sys
import json
import subprocess
import os

# ANSI Colors
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"

def get_git_info(cwd):
    try:
        # Get branch name
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], 
            cwd=cwd, stderr=subprocess.DEVNULL, text=True
        ).strip()
        
        # Get short status
        status_output = subprocess.check_output(
            ["git", "status", "--short"], 
            cwd=cwd, stderr=subprocess.DEVNULL, text=True
        ).splitlines()

        staged = 0
        unstaged = 0
        untracked = 0
        
        for line in status_output:
            if len(line) < 2: continue
            index = line[0]
            worktree = line[1]
            
            if index == "?":
                untracked += 1
            else:
                if index != " ":
                    staged += 1
                if worktree != " " and worktree != "?":
                    unstaged += 1
        
        parts = []
        if staged: parts.append(f"{GREEN}+{staged}{RESET}")
        if unstaged: parts.append(f"{YELLOW}!{unstaged}{RESET}")
        if untracked: parts.append(f"{RED}?{untracked}{RESET}")
        
        status_str = f"({' '.join(parts)})" if parts else ""
        return f" {branch} {status_str}".strip()
    except:
        return ""

def main():
    try:
        input_data = sys.stdin.read()
        if not input_data:
            return
        data = json.loads(input_data)
    except:
        return

    # 1. Extract Data
    model = data.get("model", {}).get("display_name", "Claude")
    cost = data.get("cost", {}).get("total_cost_usd", 0.0)
    ctx_pct = data.get("context_window", {}).get("used_percentage", 0)
    vim_mode = data.get("vim", {}).get("mode")
    cwd = data.get("workspace", {}).get("current_dir", os.getcwd())
    session_name = data.get("session_name", "")
    
    # 2. Format Components
    # Model & Session
    model_str = f"{CYAN}{BOLD}[{model}]{RESET}"
    session_str = f" {MAGENTA}({session_name}){RESET}" if session_name else ""
    
    # Vim Mode
    vim_str = f" {RED}{BOLD}VIM:{vim_mode}{RESET}" if vim_mode else ""
    
    # Git
    git_str = f" {BLUE}{get_git_info(cwd)}{RESET}"
    
    # Cost
    cost_str = f" {YELLOW}${cost:.3f}{RESET}"
    
    # Context Bar
    bar_size = 10
    filled = max(0, min(bar_size, int(ctx_pct / 100 * bar_size)))
    color = GREEN if ctx_pct < 60 else (YELLOW if ctx_pct < 85 else RED)
    bar = f" {color}[{'█' * filled}{'░' * (bar_size - filled)}]{RESET} {color}{ctx_pct:.0f}%{RESET}"

    # 3. Assemble Statusline
    # Format: [Model] (Session) [Bar] 80% | $0.05 |  main* | VIM:NORMAL
    output = f"{model_str}{session_str}{bar} |{cost_str} |{git_str}{vim_str} "
    
    sys.stdout.write(output)

if __name__ == "__main__":
    main()
