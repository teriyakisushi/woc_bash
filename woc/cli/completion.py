"""生成 shell 补全脚本（bash / zsh）。

只补命令名和参数，不涉及执行语义；由用户自己决定是否 source。
"""

BASH = '''# woc bash completion
# 用法: source <(woc --completion bash)
_woc_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local common="--templates --check-templates --install-templates --completion --help"
    local wocc_opts="--loop --capture-sudo --last-session --sessions --resume --help"
    case "${COMP_WORDS[0]}" in
        @WOCC@) COMPREPLY=( $(compgen -W "$wocc_opts" -- "$cur") ) ;;
        *) COMPREPLY=( $(compgen -W "$common" -- "$cur") ) ;;
    esac
}
complete -F _woc_complete @NAMES@
'''

ZSH = '''#compdef @NAMES@
# 用法: source <(woc --completion zsh)
_woc_complete() {
    local -a opts
    opts=(--templates --check-templates --install-templates --completion --help
          --loop --capture-sudo --last-session --sessions --resume)
    compadd -- $opts
}
compdef _woc_complete @NAMES@
'''

SHELLS = ("bash", "zsh")


def render(shell, names):
    """names = [woc, wocc, woc_chat]（按配置里的命令名）。"""
    joined = " ".join(names)
    if shell == "bash":
        return BASH.replace("@NAMES@", joined).replace("@WOCC@", names[1])
    if shell == "zsh":
        return ZSH.replace("@NAMES@", joined)
    raise ValueError(shell)
