# 2026-03 - BulletJournal

In-house replacement for Jupyter, for better reproducibility, reliability and traceability of complex data science.

# 📚 Context

Jupyter is a great tool for Python data processing, but it lacks several important things:

- State management is messy, an analyst can run cells out of order or inadvertently
- Lack of reproducibility: many notebooks in our past studies have been modified without re-running all dependent cells, making it pretty much impossible to reproduce the conclusions of the study from the raw datasets
- Bad reliability of Jupyter overall: many disconnections or kernels hanging silently. This happens both over the web UI and over SSH in VSCode.
- Cannot automate running multiple notebooks in a row
- In the current iteration of pipeline design, input/output format has been somehow normalized through the use of `OUTPUT_DIR`: each notebook has its dedicated dir where it has exclusive authority over writing/editing files. However, the exact format and schema of this output data is not really normalized, each notebook can adopt its own format that needs to implicitly be used by subsequent notebooks that use its output files.
- Templating specifications through TOML/ATOML files can be hard to read, a visual layout could be better
- There are strong guarantees over integrity with the current templating spec (no OUTPUT_DIR duplicates, each notebook is numbered, default parameters, …) but they are only enforced when creating the study, then the analyst takes back full control and can introduce inconsistencies and bugs.
- .ipynb files bring some friction with tooling, especially git (diffs created each time a notebook is run), GitHub (review UI is very broken) and AI coding agents (most of them have trouble with notebooks and context across multiple cells)

# 🎯 Objectives

- Build a complete pipelining solution with strong integrity guarantees across the execution of multiple scripts/notebooks, eliminating as much of the hidden-state problem as possible. When an upstream dependency (dataset file, notebook, …) is modified, the notebook is marked as stale and needs to be re-run to refresh its outputs.
- Two possible ways of running notebooks:
    - R&D / Exploration mode: the notebook is edited multiple times by the analyst to experiment various approaches and try to find new aspects of the dataset
    - Prod mode: The notebook is taken from a set of pre-made templates and is ready to run as-is
- Code and notebooks should be reusable on all scales
    - Custom functions (currently implemented in the FastReport package - probably no need to change this)
    - Notebooks
    - Pipelines containing multiple notebooks
- Code and notebooks can be managed through a version-controlled database (git or purpose-made), with the ability of peer-review and automatic testing at all three scales
- Data produced by a notebook can be passed as an input to other notebooks easily, in multiple types (at least native types like int/str/bool and pandas.DataFrames)
- Notebook outputs are persisted to disk for data exploration and persistent caching across executions (not all kernels need to be alive at the same time, and this means we can also recover from crashes without recomputing everything from the start)
- The user can also provide files, e.g. external datasets, to be used in notebooks
- Expensive operations (heavy computing, API use, …) do not run automatically on every edit
- No limits on what kind of Python code can be run: imports, network calls, …

Optional objectives:

- Notebooks can produce intermediate artifact files (diagrams, text, datasets, …) that are collected/centralized, to be used in report building
- RBAC separating permissions between studies, with proper isolation (i.e. a malicious/compromised user cannot access any information on studies other than what they already have access to, even through memory/filesystem inspection). Templates may be accessed by anyone
- Past environments can be reproduced as precisely as possible, down to the specific commit of internal libraries like FastReport
- Edit history that allows reverting recent changes (deliberate or accidental) and leverage older cached values to avoid marking everything as stale when restoring a recent version. This needs to provide a good balance between UX and disk space use.
- Ideally, make this project as versatile as possible (not specific to Agoratlas use) and make it open-source

# 🗺️ Proposed solution

We propose implementing a new platform that leverages Marimo for its reactive philosophy, with an additional layer on top to manage data flowing across multiple notebooks and asset persistence.

The platform takes inspiration from platforms like Alteryx or Dagster which have DAG-based representation of data flows. Each node represents a Marimo notebook which has inputs and outputs.

## Graph editor

We use the ReactFlow library to implement the pipeline editor, with a FastAPI backend managing the state in real time and orchestrating execution.

### Blocks

Each block represents a notebook, see the illustration below for the layout.

- The top bar shows the title of the notebook, with the notebook ID (name of the file) under it, in smaller and lighter text. The (i) button shows the block’s documentation, which is the (optional) first Markdown cell in the notebook. On the top left, a small color-coded badge indicates what type of notebook this is: either T for a pure template, T\* for a notebook based on a template but edited after, C for custom, F for file input. On hover, a small text explains what the badge means. T and T\* badges can be clicked to show the original template in read-only mode.
- Inputs on the left are defined within the notebook code, they have an expected type (DataFrame, str, int, …), and possibly a default value. Optionally, inputs that have a default value can be “hidden” from the node and available under the (+) button under other inputs, to avoid UI clutter.
- Outputs on the right are defined within the notebook code. Inputs and outputs are color-coded (the outside of the circle represents the type, and the inside represents their state: ready, stale, pending).
- On the bottom bar, there is a button to run the notebook in the background, with a dropdown that has three options:
    - “Run stale”, which runs all cells necessary to compute all non-ready outputs
    - “Run all”, to force re-running all cells
    - “Edit & Run”, to open the Marimo editor and let the user run cells at their own pace. Marimo does not expose its editor server internals, but auto-save will be enabled every second in the editor so the notebook contents will be synced to disk with a small delay.
- On the bottom bar, there is another button to view the notebook’s artifacts.

The node has a border color representing its state: green if all outputs and artifacts are ready, yellow if one or more are stale, grey if never run, red if error, pulsing blue if running (with a visual indicator to show the progression of the execution, where the top bar of the cell progressively fills its background color in green as more artifacts are marked ready).

```plaintext
┌───────────────────────────────────────┐
│                                       │
│  [T*]    Extract communities     (i)  │
│          extract_communities          │
├───────────────────────────────────────┼
│                                       │
> users_df               communities_df >
│                                       │
> stats_count                modularity >
│                                       │
> target_col                            |
│                                       │
> use_gpu                               │
│                                       │
(+)                                     |
┼───────-──────────────┬────────────────┼
│                      │                │
│ ▶ Run stale cells |v │ View artifacts │
│                      │   5 | 16 | 0   │
└───────-──────────────┼────────────────┘
```

### Adding a new block

On the top right of the screen, a round button with a big “+” allows creating a new block. Options are:

- “From template”, which open the side-panel of templates for the user to drag-and-drop panels
- “Empty notebook”, creating a nearly empty notebook with basic formatting/imports
- “Value input” (which is simply a custom notebook pre-populated with an example cell pushing an artifact from a constant value)
- “File input”, which displays a file upload button on the cell to create an input artifact. It has a single output of type file, named “file” by default.

All cells except “File input” are implemented as Marimo notebooks and can be edited as such.

Each notebook must have a title (e.g. “Parse user CSV”), and a unique ID (e.g. “parse_user_csv”) which is also the name of the notebook (parse_user_csv.py).

## Artifacts

Each notebook produces artifacts named “outputs” and “assets”, which represent the two types of data available externally to the notebook when it’s done running. Outputs are visible as ports on the right of the node, and assets are more like results of the pipeline that will be used in the final report. Under the hood, they are both the same thing, but they appear differently in the UI because they serve two different purposes. Notebooks register their own artifacts under a name/ID that is unique within the notebook. The difference between an asset and an output is the `is_output` parameter. Output artifacts are declared in the notebook itself, the server detects them automatically by parsing the AST of each cell.

```python
# Minimal artifact registration
artifacts.push(df.sample(10), name='df_sample', data_type=pd.DataFrame)
# Registering an artifact as output will show a port on the node in the editor
artifacts.push(df, name='processed_df', is_output=True, data_type=pd.DataFrame, description='Processed dataset with an additional "community" column')
# Registering an artifact as a file
with artifacts.push_file(name='dataset_viz', extension='.png') as _out_path:
    plt.savefig(_out_path)
```

Asset artifacts are made to be viewed, downloaded, or imported in a special notebook to produce the final study report. These artifacts a typically dataviz images or dataset samples, destined only to be in the report and not used in subsequent processing steps. We hide them from cell outputs to prevent clutter (high number of notebook output ports + edges drawn to the final report builder), but they are functionally identical to notebook outputs.

Artifacts are persisted to disk, along with their metadata.

Defining inputs is similar to outputs:

```python
graph_df = artifacts.pull(name='graph_df', data_type=pd.DataFrame)

num_iterations = artifacts.pull(name='n_iter', data_type=int, default=100)

_usernames_path = artifacts.pull_file(name='usernames')
with open(_usernames_path, 'r') as _usernames_file:
    user_id_list = json.loads(_usernames_file)
```

When a notebook reads a stale artifact (one that needs to be recomputed after a change upstream), a warning is displayed along with the age of the artifact (e.g. “WARNING: Loaded stale artifact “graph_df”, generated 2h13m ago (upstream code was updated 26m ago). Run upstream blocks to refresh the artifact.”)

When a notebook tries to load an unavailable (never computed) artifact, the execution raises an error.

Edges between notebooks (i.e. knowing where the imported artifact comes from) are not directly visible to the notebook itself. The server manages them separately from the notebook’s code, and the resolution is performed at runtime to load the most recent available version.

The user can also provide external artifact files by clicking an upload button, or create “constants” blocks that produce one or more values to be used as inputs to other notebooks.

To produce the final report of the study, the analyst will often need to create one last notebook to process and compile many artifacts created in other notebooks. In this case, these notebooks can use a special function to pull a specific artifact with its full path (&lt;notebook_id&gt;/&lt;artifact_name&gt;). This helps prevent clutter, since this kind of notebook may require many different inputs. When an artifact is loaded directly with its full name, a special input is shown at the top of its input ports on the left side of the node. It has a symbol with an A inside that cannot be connected, and it can be hovered/clicked to list all artifacts used and their current state (ready/stale/pending). This input is only green (ready) when all underlying artifacts are green. The pull syntax is similar to input ports, but a full path is given. This has the effect of creating “invisible edges” in the editor graph.

```python
user_summary = artifacts.pull(name='user_llm_analysis/summary', data_type=str)
```

There are 4 kinds of artifacts:

- Simple objects (int, dict, list, bool, …) are serialized to JSON.
- DataFrames are serialized to Parquet files
- Other objects are pickled and compressed
- Notebooks can also persist data to files directly. push_file and pull_file will provide absolute filenames pointing to the artifact file that can be read or written. To ensure integrity, the object storage is always read-only to the execution kernel: push_file will point to a temporary location, then the file is moved and set to read-only when its context manager is closed.

## Unified state management

The server is in charge of tracking the state of data and notebooks, to update the graph UI and status in real time.

Changes in Marimo notebooks (adding/removing inputs and outputs, changing code) can have an impact on the graph and the staleness of downstream artifacts. The server watches changes to notebook source files and artifact files, and updates the UI accordingly in real time without requiring a page reload.

## Ensuring consistency

The server computes a dependency graph for each artifact, which is kept up-to-date with every change in a notebook and every artifact exported. When anything upstream of an artifact is changed that could affect its value, the artifact is marked as stale.

The dependency graph is computed by analyzing the contents of notebooks (artifact imports/exports + dependency graph of cells exposed by Marimo’s InternalApp). Analyzing data dependencies at cell level compared to notebook level can provide time gain when exploring data: if a notebook is modified but not in a way that can affect the artifact (i.e. in a cell that is not upstream of the artifact), it is not marked as stale and does not need to be recomputed.

When updating the global dependency graph, each artifact has three hashes (SHA256 based) that are computed at different times to represent the artifact’s data and its dependencies. Let’s say a notebook has an output artifact A, that depends on input artifacts B and C.

- The “artifact hash” is a hash of the artifact’s actual data. The data hash serves as a unique pointer to the filesystem where the object is stored, which helps deduplicate identical files to reduce disk usage.
- The “upstream code hash” represents purely the computing steps required across the pipeline to produce the given artifact. In our example, for artifact A this hash will be based on the upstream code hashes of B and C, and a deterministic hash of all code cells in the notebook that lie on the dependency path from B to A or from C to A.
- The “upstream data hash” is very similar to the upstream code hash, but we use the actual value of upstream artifacts instead of their logical representation. It is based on the artifact hash of upstream artifacts, along with the same deterministic hash of code cells.

When upstream code is modified, some artifacts may remain exactly the same despite the logic being updated. The upstream data hash helps save compute time for this case: by keeping a mapping of previously encountered {upstream data hash → artifact hash} pairs, the server can immediately detect if an artifact has already been computed in the same conditions, and immediately update its artifact hash without needing to re-run the notebook (assuming the previously computed artifact is still in cache). This is conceptually quite similar to marimo.persistent_cache or functools.cache, but at a scale that spans multiple notebooks.

Any solution chosen for representing data on the server must ensure consistency and reproducibility: if the server encounters a crash or an unexpected shutdown, the full state of the editor can be recovered based on what’s on disk. This means that the server should minimize the amount of data that resides exclusively in memory. For performance considerations, it is acceptable to have a small delay between data being updated in memory vs persisted on disk, but this delay should ideally remain under a few seconds. At all times, the data persisted on disk should be loadable as a valid editor state.

### Deterministic code graph hashing

The code hashing algorithm must be as deterministic as possible, even in a graph representation where cells do not have stable IDs from one execution to another. To implement this, we compute the upstream hashes using a deterministic topological order:

- The upstream hashes of all parent cells are gathered in a list (topological order ensures they are already computed)
- This list of hashes is sorted in ascending order
- The SHA256 hash of the cell’s contents is appended to the list
- The final hash of the cell corresponds to the SHA256 hash of all concatenated values in the list

### Artifact freshness pitfall

There is an edge-case to be careful about when computing artifact freshness. Consider the following sequence of operations:

- The user opens an editor on notebook X, loads artifact A and starts to do some processing
- Notebook Y, which is the source of artifact A, is re-run while X is being edited, and changes the value of A
- Notebook X finishes running, and it generates artifact B

In this case, if we only consider timestamps of files to evaluate freshness, everything looks up-to-date because the last update times are in the order Y<A<X<B. However, B is actually out-of-date because it was computed on an outdated value of A.

This means that the artifact library called to push/pull artifacts must keep track of the versions of artifacts that were imported, which will be used to compute the correct upstream data hash, by combining the artifact hashes at import time for upstream artifacts, along with the hash of code cells at export time (while remaining careful about the small delay that can happen between execution and autosave happening every 1s).

When an artifact is pushed, the library will know exactly which version of the upstream artifacts have been used (if the import cell was run multiple times, we count the most recent import). When exporting an artifact based on stale data, the persisted artifact will be created but immediately identified as stale, and a warning is shown to the user indicating the names of upstream artifacts that need to be refreshed in the notebook.

### Nondeterministic notebook pitfall

Another interesting edge-case happens when a notebook performs nondeterministic operations (e.g. random samples, ML model calls, …), where two consecutive runs on identical code could produce two different artifact values with the exact same upstream hashes. When this happens, the server must invalidate the upstream data hash of all artifacts that depend directly on the modified artifact.

## State history and artifact caching

In addition to the current state of the pipeline, the server maintains a history of past states (notebooks+graph) to allow reverting recent actions or restoring/viewing a previous version exactly as it was. It is not necessary to persist the state at every change, but we should avoid leaving large gaps between saves to provide good UX.

The server can also keep older versions of artifacts, which can be useful to avoid recomputing everything if a previous state is restored, with a careful balance between disk usage and UX: for example, a max cache size could be defined at instance level and older/less relevant entries are evicted automatically as needed. When reverting to a previous state, artifacts that have been evicted from cache are marked as stale or unavailable (depending on whether there is another version available)

When an artifact becomes stale after its upstream hash changed, its most recent value is conserved until the updated value is computed. This means that downstream notebooks can still use it to explore data, but they will be shown a warning upon pulling the artifact.

## Templating

Templating is implemented on two levels: notebooks and pipelines. Both kinds are stored in the server configuration files (this makes it easier to deploy them from a VCS like git) and they are considered read-only by the server. They can be considered static and will not change while the server is running.

Templates appear in a collapsible left panel in the main editor, and can be dragged-and-dropped to the main panel to initialize a new block or group of blocks based on the template. The catalog has a selector at the top, to switch between pipelines and notebooks.

Notebooks created from templates need to have a unique name. For notebooks, if the default name is already taken, the editor will prompt the user for the name. For pipelines, if the default name of any notebook is already taken, it will ask for a prefix to add to all names.

### Notebook templates

Notebook templates are Marimo notebook files (.py extensions) and can be organized in sub-directories for easier navigation in the UI.

### Pipeline templates

Pipeline templates contain an entire graph or a subgraph that can be dragged-and-dropped to the editor to initialize multiple notebooks at the same time. Their data structure is very similar to the format used by the editor, with a few key differences:

- The pipeline template may contain one or more notebook templates. They are not stored as notebooks, but rather as a pointer to a notebook template. The notebook template is copied upon initialization. This ensures that we can sync changes in all pipeline templates when a notebook template is updated.
- Artifacts are not stored with the pipeline template, not even input files. File input blocks, if any, start uninitialized.

### Template integrity

A utility script is provided to ensure that templates are correct: it checks the syntax of notebooks using a generic linter and the Marimo linter, and verifies that all notebooks (especially template notebooks) contain all artifact imports/exports used in graph edges, verifies the graph is a DAG, etc. The script is run manually when committing new changes to the template repository.

An end-to-end testing strategy may be implemented later, to run tests on single notebooks or full pipelines.

## Data format

All persistent data related to a project sits within a single directory, called the project root. This directory is entirely portable, it can be transferred to another instance of the server to resume the project there without any issue.

### Artifacts

The content of artifacts is persisted in a repository similar to git objects, based on the artifact hash. The first two characters are a directory, then the rest of the hash is the name of a subdirectory. This subdirectory contains the serialized object/file and its metadata.

### Execution state

The server will juggle with three states of artifacts:

- Ready
- Stale (computed in the past but at least one upstream artifact or cell has been changed)
- Pending (never computed)

The first two kinds have an actual value persisted on disk, in the object storage based on their artifact hash.

Artifacts always have an upstream code hash, which is deduced entirely from the dependency graph. File artifacts coming from a file input cell have an upstream code hash equal to their current artifact hash (or all zeroes if not initialized).

When a pending artifact has all its immediate parent artifacts ready, its upstream data hash can be computed:

- If the upstream data hash matches a previously computed artifact in cache, the new artifact does not need to be recomputed and immediately gets the artifact hash pointing to the cached artifact.
- Otherwise, it will need to be computed to create the artifact on disk and get its artifact hash.

Along with the artifact object cache, we maintain a file/database on disk mapping these hashes together, so the server can reconstruct the full state of execution and artifacts after a cold start.

### Notebooks

Each block (except file input) corresponds to a Marimo notebook. All notebooks are stored in the same directory.

### Graph

The graph structure of notebooks is stored independently from the notebook contents and artifacts. It is persisted to disk (maybe as a human-readable format like JSON, TOML, YAML?)

### Checkpoints

Checkpoints are a copy of the graph (graph.json + notebooks) that saves snapshots of the editor state at regular intervals. When an edit is performed on the graph or a notebook and no snapshot was saved in the last N minutes, a new snapshot is made.

The file structure of a project is as follows:

```
project_root/
├─ graph.json
├─ checkpoints/
│  ├─ 2026-03-11_17:13:46/
│  │  ├─ graph.json
│  │  ├─ notebooks/
│  │  │  ├─ notebook_1.py
├─ notebooks/
│  ├─ notebook_1.py
│  ├─ notebook_2.py
│  ├─ notebook_3.py
├─ artifacts/
│  ├─ a1/
│  │  ├─ d8098ceddc122be684385ff870f8d361a072ff5cbdf72661f2c27e9c1bb994/
│  │  │  ├─ metadata.json
│  │  │  ├─ data
│  ├─ hashes.db
├─ metadata/
│  ├─ project.json
│  ├─ requirements.txt
```

## Exploring artifacts

The editor also needs to provide convenient ways to explore artifact values.

- When hovering over an edge or an input/output port with a computed artifact (ready or stale), a summary of the artifact is displayed:
    - Simple objects are shown entirely, or cropped if their representation is too long
    - DataFrames show a preview of the first few rows and columns, along with the total number of rows/cols
    - Image files, if less than 1MB, are displayed as the preview
    - Files and other objects do not need to have a pretty-printed value but there should still be basic info shown

The preview is persisted in the object store, in the artifact’s metadata.

- When clicking “View artifacts” on a cell, the user sees a list containing each output artifact, along with their color-coded state (ready, stale, pending), the preview, and buttons to download the artifact.
- A similar “View artifacts” page can also be used to explore all artifacts across all notebooks.

## Multi-execution

The server also offers the ability to automatically run cells across multiple notebooks. During a run, it keeps a queue in memory containing all notebooks to run, and which artifacts need to be refreshed.

On the top right of the main editor window, there is a round button with a large green Play symbol. It first requests confirmation from the user to run all pending/stale cells, then queues them in an order fitting the dependency graph.

The execution continues even if the user closes their browser. When running, the button becomes a red Stop that can kill the execution at any time. The same mechanism is used when the user clicks the “Run stale” / “Run all” button on a node.

If any error is encountered on any cell, the execution is stopped entirely.

If the graph is modified in a way that modifies the dependency graph of any queued artifact, the execution also stops (after requesting confirmation from the user if the modification happens in the main editor).

Each task in the queue represents a notebook, and which of its output artifacts we need refreshed. A dedicated process takes this information, then determines an execution plan based on the dependency graph inside the given notebook and executes it.

## Security

Each project must be designed to run in its own independent environment if needed, e.g. a Docker container. Implementing the management of multiple independent deployments is not a priority, but the project may evolve later for multi-user needs.

Analysts will be running arbitrary Python code in their projects. While basic security measures should be used wherever possible, it is accepted that they could run malicious code to break the way the server works, or affect the integrity of the project data by reading/writing arbitrary locations in the filesystem or memory. This is fine, as long as this remains strictly contained to the project environment (if running in containers).

## Design choices

### Grid

The ReactFlow editor has a dot grid background. Block coordinates, sizes and layouts are based on this “unit block” length (everything snaps to the grid).

### Artifact explorer

Next to the Play button in the top right, a global summary of artifacts is shown (how many in each state). When clicked, the user is taken to the artifact explorer.

The user can right-click on an input or output node to run some actions on its underlying artifact:

- Only when artifact is ready: “Force refresh artifact“ (asks for confirmation, then deletes the artifact and queues cells in its dependency graph where the artifact is generated)
- Only when stale or pending: “Generate artifact” (same, but without deleting the fresh value)
- Force delete artifact (asks for confirmation, then deletes the artifact)
- View artifact
- Download artifact

### Running with stale inputs

When a user clicks “Run all” / “Run stale cells” / “Edit & Run” on a notebook where not all inputs are green, a warning will ask confirmation from the user: “Some inputs are stale or pending, do you want to run upstream notebooks to refresh them? (XX cells total for YY inputs)” with options “Yes”, “Use stale data”, “Cancel”. If the button was “Edit & Run” and the user selects Yes, the execution of all upstream cells is queued but no editor opens (the user will click again when cells are done running).

## AI integration

In this initial version, we will not focus on implementing mechanisms allowing AI agents to autonomously run studies, but this will be considered in a future update (probably as an MCP server)