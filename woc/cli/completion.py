"""生成 shell 补全脚本（bash / zsh）。

只补命令名和参数，不涉及执行语义；由用户自己决定是否 source。
`woc-fs` 不单独占一个配置键，名字从 woc 命令名派生（`<woc_prefix>-fs`）。
"""

BASH = '''# woc bash completion
# 用法: source <(woc --completion bash)
_woc_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local common="--templates --check-templates --install-templates --completion --help"
    local wocc_opts="--loop --capture-sudo --last-session --sessions --resume --help"
    local fs_opts="read write edit --lines --head --tail --max-chars --raw --content --mkdir --backup --append --dry-run --old --new --all"
    case "${COMP_WORDS[0]}" in
        @WOCC@) COMPREPLY=( $(compgen -W "$wocc_opts" -- "$cur") ) ;;
        @FS@) COMPREPLY=( $(compgen -W "$fs_opts" -- "$cur") ) ;;
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
          --loop --capture-sudo --last-session --sessions --resume
          read write edit --lines --head --tail --max-chars --raw --content
          --mkdir --backup --append --dry-run --old --new --all)
    compadd -- $opts
}
compdef _woc_complete @NAMES@
'''

SHELLS = ("bash", "zsh")


def render(shell, names):
    """names = [woc, wocc, woc_chat]（按配置里的命令名）；woc-fs 由第一个派生。"""
    all_names = list(names) + [names[0] + "-fs"]
    joined = " ".join(all_names)
    if shell == "bash":
        return (BASH.replace("@NAMES@", joined)
                    .replace("@WOCC@", names[1])
                    .replace("@FS@", all_names[-1]))
    if shell == "zsh":
        return ZSH.replace("@NAMES@", joined)
    raise ValueError(shell)
