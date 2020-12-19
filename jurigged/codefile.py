import ast
import inspect
import itertools
import re
import textwrap
from dataclasses import dataclass, field, replace as dc_replace
from types import FunctionType, ModuleType

from ovld import has_attribute, ovld


def _split(text):
    return re.findall(".*\n", text)


@ovld.dispatch
def conform(self, obj1, obj2):
    if hasattr(obj1, "__conform__"):
        obj1.__conform__(obj2)
    else:
        self.resolve(obj1, obj2)(obj1, obj2)


@ovld
def conform(self, obj1: FunctionType, obj2: FunctionType):
    obj1.__code__ = obj2.__code__


@ovld
def conform(self, obj1: type, obj2: type):
    v1 = dict(vars(obj1).items())
    for k, v in vars(obj2).items():
        if k in v1:
            self(v1[k], v)
        else:
            setattr(obj1, k, v)


@ovld
def conform(self, obj1, obj2):
    pass


class IDSet:
    def __init__(self):
        self._data = {}

    def add(self, x):
        self._data[id(x)] = x

    def remove(self, x):
        del self._data[id(x)]

    def __iter__(self):
        return iter(self._data.values())


@ovld.dispatch(initial_state=lambda: {"seen": set()})
def dig(self, obj, module_name):
    if id(obj) in self.seen:
        return set()
    elif hasattr(obj, "__functions__"):
        return set(obj.__functions__)
    else:
        self.seen.add(id(obj))
        return self[type(obj), object](obj, module_name)


@ovld
def dig(self, obj: FunctionType, module_name):
    rval = {inspect.unwrap(obj)}
    for x in obj.__closure__ or []:
        rval.update(self(x.cell_contents, module_name))
    return rval


@ovld
def dig(self, obj: ModuleType, module_name):
    rval = set()
    if obj.__name__ == module_name:
        for value in vars(obj).values():
            rval.update(self(value, module_name))
    return rval


@ovld
def dig(self, obj: type, module_name):
    rval = {obj}
    if obj.__module__ == module_name:
        for value in vars(obj).values():
            rval.update(self(value, module_name))
    return rval


@ovld
def dig(self, obj: (classmethod, staticmethod), module_name):
    return self(obj.__func__, module_name)


@ovld
def dig(self, obj: property, module_name):
    return (
        self(obj.fget, module_name)
        | self(obj.fset, module_name)
        | self(obj.fdel, module_name)
    )


@ovld
def dig(self, obj: object, module_name):
    return set()


@dataclass
class Definition:
    type: str
    name: str
    filename: str
    firstlineno: int
    lastlineno: int
    nlock: int
    parent: "Optional[Definition]"
    children: list = field(compare=False)
    indent: int
    source: str
    saved: str
    live: str
    node: object = field(compare=False)
    object: object
    pred: "Optional[Definition]" = field(compare=False, default=None)
    succ: "Optional[Definition]" = field(compare=False, default=None)

    replace = dc_replace

    def __post_init__(self):
        self.activate(self.source)

    @property
    def active(self):
        return self.live is not None

    def predecessor(self, filename):
        pred = self.pred
        while pred is not None and (
            not pred.active or pred.filename != filename
        ):
            pred = pred.pred
        return pred

    def successor(self, filename):
        succ = self.succ
        while succ is not None and (
            not succ.active or succ.filename != filename
        ):
            succ = succ.succ
        return succ

    def parent_chain(self):
        if self.parent is None:
            return []
        else:
            return [self.parent, *self.parent.parent_chain()]

    def activate(self, source):
        self.live = self.source = source
        if self.parent is not None and self not in self.parent.children:
            self.parent.children.append(self)

    def deactivate(self):
        self.live = None
        if self.parent is not None:
            self.parent.children.remove(self)
        for child in self.children:
            child.deactivate()

    def scope(self):
        yield self
        for child in self.children:
            yield from child.scope()

    def sourcelines(self):
        return self.source.split("\n")

    def lockedlines(self):
        return self.sourcelines()[: self.nlock]

    def corresponds(self, defn):
        assert isinstance(defn, Definition)
        if self.type != defn.type:
            return False
        srcchk = self.type != "statement" or self.source == defn.source
        return (
            self.name == defn.name
            and srcchk
            and (
                self.parent is defn.parent
                or self.parent is not None
                and self.parent.corresponds(defn and defn.parent)
            )
        )

    def compatible(self, defn):
        return self.lockedlines() == defn.lockedlines()

    def refile(self, filename, lineno):
        offset = lineno - self.firstlineno
        old_filename = self.filename
        for defn in self.scope():
            assert defn.filename == old_filename
            defn.filename = filename
            defn.renumber(defn.firstlineno + offset)

    def renumber(self, firstlineno, lastlineno=None, all_lines=None):
        assert firstlineno > 0
        obj = self.object
        if isinstance(obj, FunctionType):
            obj.__code__ = obj.__code__.replace(
                co_firstlineno=firstlineno, co_filename=self.filename
            )
        if lastlineno is None:
            delta = firstlineno - self.firstlineno
            lastlineno = self.lastlineno + delta

        self.firstlineno = firstlineno
        self.lastlineno = lastlineno

        for defnp in self.parent_chain():
            assert defnp.active
            defnp.lastlineno = max(d.lastlineno for d in defnp.children)

    def evaluate(self, glb):
        code = compile(
            (
                self.node
                if isinstance(self.node, ast.Module)
                else ast.Module(body=[self.node], type_ignores=[])
            ),
            mode="exec",
            filename=self.filename,
        )
        if self.name is None:
            exec(code, glb, glb)
        else:
            lcl = {}
            exec(code, glb, lcl)
            return lcl[self.name]

    def format_lines(self):
        src = textwrap.indent(self.live, " " * self.indent)
        if not src.endswith("\n"):
            src += "\n"
        return _split(src)


@dataclass
class Info:
    filename: str
    source: str
    parent: Definition
    lines: list = None

    def __post_init__(self):
        self.lines = self.source.split("\n")

    replace = dc_replace


def _definition_from_node(node, info, **fields):
    if hasattr(node, "decorator_list"):
        firstlineno = min(
            [deco.lineno for deco in node.decorator_list] + [node.lineno]
        )
    else:
        firstlineno = node.lineno
    src = "\n".join(info.lines[firstlineno - 1 : node.end_lineno])
    indent = len(src) - len(src.lstrip())
    src = textwrap.dedent(src)
    defn = Definition(
        **fields,
        name=getattr(node, "name", None),
        filename=info.filename,
        firstlineno=firstlineno,
        lastlineno=node.end_lineno,
        nlock=node.lineno - firstlineno,
        parent=info.parent,
        children=[],
        indent=indent,
        source=src,
        saved=src,
        live=src,
        node=node,
        object=None,
    )
    return defn


@dataclass
class Clustered:
    members: list


def _cluster_lines(nodes):
    """Put nodes with overlapping lines together.

    If two consecutive nodes have overlapping lineno/end_lineno, they will be consolidated into a
    Clustered instance, which will then be turned into a single definition by collect_definitions.
    """
    if len(nodes) == 0:
        return nodes
    results = []
    endline = -1
    for node in nodes:
        if node.lineno <= endline:
            curr.append(node)
        else:
            curr = [node]
            results.append(curr)
        endline = node.end_lineno
    return [Clustered(r) if len(r) > 1 else r[0] for r in results]


@ovld
def collect_definitions(self, nodes: list, info: Info):
    defns = [self(node, info) for node in _cluster_lines(nodes)]
    curr = None
    for defn in defns:
        if isinstance(defn, Definition):
            defn.pred = curr
            if curr is not None:
                curr.succ = defn
            curr = defn
    return defns


@ovld
def collect_definitions(
    self, node: (ast.FunctionDef, ast.AsyncFunctionDef), info: Info
):
    # Note: we do not go inside to collect closure definitions
    return _definition_from_node(node, info, type="function")


@ovld
def collect_definitions(self, node: ast.ClassDef, info: Info):
    defn = _definition_from_node(node, info, type="class")
    # Note: we DO go inside class definitions to collect methods
    defn.children = self(node.body, info.replace(parent=defn))
    return defn


@ovld
def collect_definitions(self, node: ast.Module, info: Info):
    return self(node.body, info)


@ovld
def collect_definitions(self, node: ast.stmt, info: Info):
    return _definition_from_node(node, info, type="statement")


@ovld
def collect_definitions(self, cluster: Clustered, info: Info):
    stmts = cluster.members
    node = ast.Module(
        body=stmts,
        lineno=stmts[0].lineno,
        end_lineno=stmts[-1].end_lineno,
        type_ignores=[],
    )
    return _definition_from_node(node, info, type="statement")


def _flatten(entries):
    results = []
    if not isinstance(entries, list):
        results.append(entries)
        entries = entries.children
    for entry in entries:
        results += _flatten(entry)
    return results


class CodeFile:
    def __init__(self, filename, source=None, logger=None):
        self.globals = None
        self.logger = logger
        self.filename = filename
        self.filenames = {filename}
        if source is None:
            source = open(filename).read()
        if not source.endswith("\n"):
            source += "\n"
        self.source = source
        self.next_lines = _split(source)
        tree = ast.parse(source, filename=filename)
        results = _flatten(
            collect_definitions(
                tree, Info(filename=filename, source=source, parent=None)
            )
        )
        self.defnmap = {}
        self.definitions = IDSet()
        for defn in results:
            self.add_definition(defn)

    def emit(self, *args):
        if self.logger is not None:
            self.logger(self, *args)

    def add_definition(self, defn, redirect=None):
        key = defn.name, defn.firstlineno
        value = redirect or defn
        self.definitions.add(value)
        self.defnmap[key] = value

    def locate(self, obj):
        """Locate the Definition for this object, based on name/line number.

        * A function is located using its code object's name/filename/line number.
        * A class is located by first locating one of its methods. It will not be
          matched to a Definition otherwise (that would be a TODO).
        """
        if isinstance(obj, FunctionType):
            code = obj.__code__
            if code.co_filename not in self.filenames:
                return None
            key = (obj.__name__, code.co_firstlineno)
            return self.defnmap.get(key, None)

        elif isinstance(obj, type):
            for method in vars(obj).values():
                defn = self.locate(method)
                if defn is not None:
                    return defn.parent
            else:
                return None

        else:
            return None

    def associate(self, obj, module_name=None):
        module_name = module_name or self.module.__name__
        for x in dig(obj, module_name):
            defn = self.locate(x)
            if defn is not None:
                defn.object = x

    def discover(self, module):
        """Find and associate this CodeFile's definitions to the module's objects.

        The module is searched for functions and classes. If a function's name and
        line number match a definition in the CodeFile, that definition is updated
        to point to the function or class.

        Arguments:
            module: A module object from which to get definitions.
        """
        self.module = module
        self.globals = vars(module)
        self.associate(module)

    def match_definitions(self, codefile, update_parents=False):
        """Match up definitions from another codefile object.

        Arguments:
            codefile: Another CodeFile instance (possibly a new version of
                the same file).

        Returns:
            pairs: A list of (self.defn, codefile.defn) pairs that match each
                other (that is to say, the second is a valid update of the first)
            additions: A list of new definitions from the new codefile that could
                not be found in self.definitions.
            deletions: A list of definitions in this CodeFile that were not found
                in the other.
        """
        defns1 = list(self.definitions)
        defns2 = list(codefile.definitions)
        same = []
        changes = []
        deletions = []
        backmap = {}
        for defn1 in defns1:
            for defn2 in list(defns2):
                if defn1.corresponds(defn2):
                    if defn1.live == defn2.live:
                        same.append((defn1, defn2))
                    elif defn1.type == "class":
                        # Classes are never considered changed
                        same.append((defn1, defn2))
                    else:
                        assert defn1.type == "function"
                        assert defn1.object is not None
                        changes.append((defn1, defn2))
                    if defn1.active:
                        backmap[id(defn2)] = defn1
                    defns2.remove(defn2)
                    break
            else:
                if defn1.active:
                    deletions.append(defn1)

        deletions = [defn for defn in deletions if defn.parent not in deletions]

        additions = [
            defn
            for defn in defns2
            if defn.parent is None or id(defn.parent) in backmap
        ]
        if update_parents:
            for defn in additions:
                if defn.parent:
                    defn.parent = backmap[id(defn.parent)]
            for defn in codefile.definitions:
                defn.pred = backmap.get(id(defn.pred), defn.pred)
                defn.succ = backmap.get(id(defn.succ), defn.succ)

        return same, changes, additions, deletions

    def merge(self, codefile, partial=False):
        self.filenames.update(codefile.filenames)
        same, changes, additions, deletions = self.match_definitions(
            codefile, update_parents=True
        )
        if partial:
            deletions = []

        for defn in additions:
            self._process_addition(defn)

        for d1, d2 in changes:
            self._process_change(d1, d2)

        for d1, d2 in same:
            self._process_same(d1, d2)

        for defn in deletions:
            self._process_deletion(defn)

    def link(self, defn, obj):
        defn.activate(defn.source)
        if defn.parent is None:
            self.globals[defn.name] = obj
        else:
            setattr(defn.parent.object, defn.name, obj)

    def unlink(self, defn):
        defn.deactivate()
        if hasattr(defn.object, "__conform__"):
            defn.object.__conform__(None)
        elif defn.name is None:
            pass
        elif defn.parent is None:
            del self.globals[defn.name]
        else:
            parent = defn.parent.object
            delattr(parent, defn.name)

    def _insert_point(self, defn):
        pred = defn.predecessor(self.filename)
        succ = defn.successor(self.filename)
        if pred is not None:
            assert pred.parent is defn.parent
            return pred.lastlineno
        elif succ is not None:
            assert succ.parent is defn.parent
            return succ.firstlineno - 1
        elif defn.parent is not None:
            return defn.parent.lastlineno
        else:
            return len(self.next_lines)

    def _process_change(self, d1, d2):
        if not d1.compatible(d2):
            self.emit("update", d1, False)
            return
        d1.activate(d2.live)
        d2.node.decorator_list = []
        new = d2.evaluate(self.globals)
        conform(d1.object, new)
        self.add_definition(d2, redirect=d1)

        if d2.filename != self.filename:
            self.reline(d1, d1.firstlineno - 1, d1.lastlineno)
        else:
            self.reline(d1, d2.firstlineno - 1, d2.lastlineno)

        self.emit("update", d1, True)

    def _process_same(self, d1, d2):
        if d2.filename == self.filename:
            d1.renumber(d2.firstlineno)

    def _process_addition(self, defn):
        new = defn.evaluate(self.globals)
        self.link(defn, new)
        if defn.filename != self.filename:
            # It came from a different "file", so we set saved=None to
            # indicate it's not in the main codefile
            defn.saved = None

        for defn2 in defn.scope():
            self.add_definition(defn2)
        self.associate(new)

        if defn.filename != self.filename:
            insert_point = self._insert_point(defn)
            defn.refile(self.filename, insert_point + 1)
            self.reline(defn, insert_point, insert_point)

        self.emit("add", defn, True)

    def _process_deletion(self, defn):
        for defn2 in defn.scope():
            self.definitions.remove(defn2)

        self.unlink(defn)
        if defn.filename == self.filename:
            self.reline(defn, defn.firstlineno - 1, defn.lastlineno)

        self.emit("delete", defn, True)

    def reline(self, defn, start, end):
        lines = defn.format_lines() if defn.active else []
        self.next_lines[start:end] = lines
        self.renumber(
            start + 1,
            len(lines) - (end - start),
            exclude={id(d) for d in defn.scope()},
        )
        assert defn.filename == self.filename
        defn.renumber(start + 1, start + len(lines), all_lines=self.next_lines)
        for defnp in defn.parent_chain():
            new_source = "".join(
                self.next_lines[defnp.firstlineno - 1 : defnp.lastlineno]
            )
            defnp.activate(new_source)

    def renumber(self, line_min, delta, exclude=set()):
        """Shift line numbers by delta after line_min.

        This is done to update the definitions to match the true line numbers
        in the file after it is modified.
        """
        for defn in self.definitions:
            if (
                id(defn) not in exclude
                and defn.filename == self.filename
                and defn.active
            ):
                if defn.firstlineno >= line_min:
                    defn.renumber(defn.firstlineno + delta)

    def commit(self):
        new_source = "".join(self.next_lines)
        with open(self.filename, "w") as f:
            f.write(new_source)
        self.source = new_source
        for defn in self.definitions:
            if defn.live:
                defn.saved = defn.source

    def refresh(self):
        try:
            cf = CodeFile(self.filename)
        except SyntaxError as err:
            self.emit("error", err, False)
            return False
        self.merge(cf)
        return True