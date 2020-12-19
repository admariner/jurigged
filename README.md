
# jurigged

Jurigged lets you update your code while it runs. Using it is trivial:

1. `python -m jurigged your_script.py`
2. Change some function or method with your favorite editor and save the file
3. Jurigged will hot patch the new function into the running script

Jurigged updates live code smartly: changing a function or method will fudge code pointers so that all existing instances are simultaneously modified to implement the new behavior. When modifying a module, only changed lines will be re-run.


## Install

```bash
pip install jurigged
```


## Command line

The simplest way to use jurigged is to add `-m jurigged` to your script invocation, or to use `jurigged` instead of `python`.

```bash
python -m jurigged script.py

OR

jurigged script.py
```

With no arguments given, it will start a live REPL:

```bash
python -m jurigged
```


## API

You can call `jurigged.watch()` to programmatically start watching for changes. This should also work within IPython or Jupyter as an alternative to the `%autoreload` magic.

```python
import jurigged
jurigged.watch()
```

By default all files in the current directory will be watched, but you can use `jurigged.watch("script.py")` to only watch a single file, or `jurigged.watch("/*.py")` to watch all modules.


## What is it for?

* **Debugging:** Add `print` statements and `breakpoint()` to your code while it's running
* **Development:** Develop new functionality and test it live without restarting your server
* **Investigation:** Log additional metrics in running machine learning experiments


## Caveats

Jurigged works in a surprisingly large number of situations, but there are several cases where it won't work, or where problems may arise:

* **Functions that are already running will keep running with the existing code.** Only the next invocations will use the new code.
  * When debugging with a breakpoint, functions currently on the stack can't be changed.
  * A running generator or async function won't change.
  * You can use [reloading](https://github.com/julvo/reloading) in addition to Jurigged if you want to be able to modify a running for loop.
* **Changing initializers or attribute names may cause errors on existing instances.**
  * Jurigged modifies all existing instances of a class, but it will not re-run `__init__` or rename attributes on existing instances, so you can easily end up with broken objects (new methods, but old data).
* **Existing closures can't be changed.**
  * Unlike top-level functions where all existing pointers will automagically use the new code, *existing* closures will keep using their old code.
  * Here, by "closure" I mean a function defined inside another function, and by "existing" I mean closure instances that were already returned by their enclosing function or stored in a data structure, so they're basically in the wild and jurigged can't see them.
* **Decorators cannot be changed.**
  * Most wrapped/decorated functions can be changed, but that's because jurigged plows through closures to find the original functions and changes them in place (usually that does the trick). So even though it works on many decorated functions, jurigged does *not* re-run decorators, and because of this it will refuse to update if the decorators have changed.
  * Workaround: you can delete a function, save, paste it back, save again (in short: Ctrl+X, Ctrl+S, Ctrl+V, Ctrl+S). Jurigged will forget about the function once it's deleted and then it will count as an addition rather than a change. In that case it will run the new decorators. However, existing pointers to the old function won't be updated.
* **Decorators that look at/tweak function code will probably not update properly.**
  * Wrappers that try to compile/JIT Python code won't know about jurigged and won't be able to redo their work for the new code.
  * They can be made to work if they set the (jurigged-specific) `__conform__` attribute on the old function. `__conform__` takes a reference to the function that should replace it.


## How it works

In a nutshell, jurigged works as follows:

1. Insert an import hook that collects and watches source files.
2. Parse a source file into a set of definitions.
3. Crawl through a module to find function objects and match them to definitions.
   * It will go through class members, follow functions' `__wrapped__` and `__closure__` pointers, and so on.
4. When a file is modified, re-parse it into a set of definitions and match them against the original, yielding a set of changes, additions and deletions.
5. For a change, exec the new code (with the decorators stripped out), then take the resulting function's internal `__code__` pointer and shove it into the old one (this may not work on closures, but jurigged ignores closures).
6. New additions are run in the module namespace.


## Comparison

The two most comparable implementations of Jurigged's feature set that I could find (but it can be a bit difficult to find everything comparable) are **%autoreload** in IPython and **[limeade](https://github.com/CFSworks/limeade)**. Here are the key differences:

* They both re-execute the entire module when its code is changed. Jurigged, by contrast, surgically extracts changed functions from the parse tree and only replaces these. It only executes new or changed statements in a module.
  
  Which is better is somewhat situation-dependent: on one hand, re-executing the module will pick up more changes. On the other hand, it will reinitialize module variables and state, so certain things might break. Jurigged's approach is more conservative and will only pick up on modified functions, but it will not touch anything else, so I believe it is less likely to have unintended side effects. It also tells you what it is doing :)

* They will re-execute decorators, whereas Jurigged will instead dig into them and update the functions it finds inside.
  
  Again, there's no objectively superior approach. `%autoreload` will properly re-execute changed decorators, but these decorators will return new objects, so if a module imports an already decorated function, it won't update to the new version. If you only modify the function's code and not the decorators, however, Jurigged will usually be able to change it inside the decorator, so all the old instances will use the new behavior.

* They rely on synchronization points, whereas Jurigged can be run in its own thread.

  This is a double-edged sword, because even though Jurigged can add live updates to existing scripts with zero lines of additional code, it is not thread safe at all (code could be executed in the middle of an update, which is possibly an inconsistent state).

Other similar efforts:

* [reloading](https://github.com/julvo/reloading) can wrap an iterator to make modifiable for loops. Jurigged cannot do that, but you can use both packages at the same time.