import re
import os
import sublime

from .subprocess_repl import SubprocessRepl

def get_settings():
    return sublime.load_settings("SublimeHaskell.sublime-settings")

def get_setting(key, default=None):
    "This should be used only from main thread"
    # Get setting
    return get_settings().get(key, default)

def ghci_package_db():
    dev = get_setting('use_cabal_dev')
    box = get_setting('cabal_dev_sandbox')
    if dev and box:
        package_conf = (filter(lambda x: re.match('packages-(.*)\.conf', x), os.listdir(box)) + [None])[0]
        if package_conf:
            return os.path.join(box, package_conf)
    return None

def ghci_append_package_db(cmd):
    package_conf = ghci_package_db()
    if package_conf:
        cmd.extend(['-package-db', package_conf])
    return cmd

def ghci_get_min_whitespace_prefix(lines):
    line_spaces = [len(line) - len(line.lstrip()) for line in lines]
    if not line_spaces:
        return 0
    min_spaces = min(line_spaces)
    return min_spaces

def can_inject_let(s):
    func_def = "\A([a-z](\w|['_])*[ ]).*[=][ ]"
    func_sig = "\A([a-z](\w|['_])*[ ])\s*::[ ]"
    data_def = "\A(data|newtype|type|class|instance)\s+"
    let_def = "\Alet "
    return (re.search(func_def, s) or re.search(func_sig, s)) and (not re.search(data_def, s)) and (not re.search(let_def, s))

def ghci_inject_let_multiline(line, in_let_block = None):
    if (in_let_block == False) or (not can_inject_let(line)):
        return (line, False)
    return (("    " if in_let_block else "let ") + line, True)

def ghci_inject_let(lines):
    fixed_lines = [line for line in lines if not line.isspace()]

    letprefix =   "let "
    spaceprefix = "    "

    if fixed_lines and (not fixed_lines[0].startswith('let ')) and can_inject_let(fixed_lines[0]):
        injected_lines = [letprefix + fixed_lines[0]]
        injected_lines.extend([spaceprefix + l for l in fixed_lines[1:]])
        return injected_lines
    return fixed_lines

def ghci_remove_whitespace(lines):
    # remove lines that are completely whitespace
    lines = [line for line in lines if not line.isspace()]

    # remove extra whitespace for more flexible block execution
    min_spaces = ghci_get_min_whitespace_prefix(lines)

    # remove the minimum number of spaces over all lines from each
    fixed_lines = [line[min_spaces:] for line in lines]
    return fixed_lines

def ghci_wrap_multiline_syntax(lines):
    # wrap in mutli-line syntax if more than one line
    if len(lines) <= 1:
        return lines
    fixed_lines = [":{"] + lines + [":}"]
    return fixed_lines

def locate_haskell_project(current_dir):
    # Find *.cabal file in parent folders of 'current_dir'
    cur_dir = current_dir
    while True:
        for name in os.listdir(cur_dir):
            (file_name, ext) = os.path.splitext(name)
            if file_name and ext == '.cabal':
                return cur_dir
        last_dir = cur_dir
        cur_dir = os.path.dirname(cur_dir)
        if last_dir == cur_dir:
            return None

class SublimeHaskellRepl(SubprocessRepl):
    TYPE = "sublime_haskell"

    def __init__(self, encoding, cmd=None, **kwds):
        if 'opts' in kwds:
            for opt in kwds['opts']:
                if opt == 'locate_project':
                    proj_path = locate_haskell_project(kwds['cwd'])
                    if proj_path:
                        kwds['cwd'] = proj_path
            del(kwds['opts'])
        super(SublimeHaskellRepl, self).__init__(encoding, cmd=ghci_append_package_db(cmd), **kwds)

        self.multiline_mode = False
        self.in_let_block = None
        self.supress_multiline_prompt = 0

    def write(self, command):
        setting_multiline = get_setting('format_multiline', True)
        setting_trimwhitespace = get_setting('format_trim_whitespace', True)
        setting_injectlet = get_setting('format_inject_let', True)

        new_cmd = ""
        if command.isspace() or (not setting_multiline and not setting_trimwhitespace):
            new_cmd = command
        else:
            lines = command.splitlines(False)
            if lines == [':{']: # start multiline mode
                self.multiline_mode = True
                self.in_let_block = None
            if lines == [':}']: # end multiline mode
                self.multiline_mode = False
            if setting_trimwhitespace:
                lines = ghci_remove_whitespace(lines)
            if setting_injectlet:
                if self.multiline_mode and len(lines) == 1:
                    (line_let, new_in_block) = ghci_inject_let_multiline(lines[0], self.in_let_block)
                    self.in_let_block = new_in_block
                    lines[0] = line_let
                else:
                    lines = ghci_inject_let(lines)
            if setting_multiline:
                lines = ghci_wrap_multiline_syntax(lines)
                self.supress_multiline_prompt = len(lines) - 1
            new_cmd = os.linesep.join(lines) + os.linesep
        return super(SublimeHaskellRepl, self).write(new_cmd)

    def read_bytes(self):
        result = super(SublimeHaskellRepl, self).read_bytes()
        if self.supress_multiline_prompt == 0:
            return result
        else:
            bs = result
            while self.supress_multiline_prompt != 0:
                chunk = bs + super(SublimeHaskellRepl, self).read_bytes()
                chunks = chunk.split(sep = b'| ', maxsplit = self.supress_multiline_prompt)
                self.supress_multiline_prompt = self.supress_multiline_prompt - (len(chunks) - 1)
                bs = chunks[-1]
            return bs + super(SublimeHaskellRepl, self).read_bytes()
