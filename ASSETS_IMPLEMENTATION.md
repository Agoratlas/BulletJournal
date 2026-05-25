# Assets

## Overview

This document contains the specification for a new BulletJournal feature called "assets".

Assets will be similar to artifacts in the way they are declared, created and stored, the main difference being their respective purposes:
- Artifacts are made to propagate data between notebooks in the data processing pipeline. Most artifacts are designed to contain raw data that is useful for one or more other notebooks down the line.
- Assets are made to convey information back to the analyst, to help them visualize the results along the pipeline, explore interesting patterns, export visuals for publication, ...

Assets can be viewed in a dashboard, each represented as a panel in the dashboard. They can represent a wide range of types, such as:
- Dataviz (histogram, scatter plot, ...)
- DataFrame
- Markdown
- HTML
- Image

Assets are created and registered like this:

```python
hist = assets.Histogram(df, x='col_x')
assets.push(hist, name='example_hist', title='Example histogram', description='Density of col_x in the example dataset')
```

Assets can also be grouped into collections that are pushed together:

```python
coll = assets.Collection()
for df in dataframes:
    _asset = assets.Scatter(df, x='col_x', y='col_y')
    coll.add_asset(_asset)
assets.push(coll, name='scatter_collection', title='Scatterplot collection example', description='Multiple assets grouped together!')
```

Note: there is currently a unused subtype of artifacts called "assets", which was an early attempt at implementing a similar feature. This subtype should be removed in favor of the new assets module.

## Implementation

For each asset type, the following elements need to be implemented:
- Python constructor
- React element
- Server-side data preparation function (for interactive elements only)

## Modifiers

The appearance and behavior of each asset can be adjusted via modifiers. Modifiers can be pre-populated via the Python object at render time, or overridden in the dashboard. A few examples:
- Number of bins in a histogram
- Range filter on a specific column of the input DataFrame
- Sort by descending values on another column of the DataFrame
- Pagination data (page number or page size) when displaying a long DataFrame

Modifiers can be changed within a dashboard, but these overrides are only local to the dashboard: the stored asset object is read-only and keeps its original modifiers. Overrides for a given panel can also be cleared to restored the original settings.

Modifiers are the only way of changing the visual output of an asset: the triplet of (asset definition, backing dataset, modifiers) should always result in the same frontend output. Asset definition and backing dataset are immutable, so only modifiers can be changed to update the visual output.

Modifiers are synchronized: when a modifier is changed in the frontend, it is persisted in the backend (the API may also return updated data in the case of interactive assets).

## Interactivity

Some assets (especially dataviz panels) can optionally be made interactive: in this case, when the asset is registered, it also saves the underlying data (typically a DataFrame) to the data storage used by artifacts.

Non-interactive assets rely on a static representation of the data. This can be useful to reduce disk usage when working with large datasets, when the asset is not intended to be refined further by the data analyst.

DataFrame assets are always interactive.

Some modifiers (e.g. filtering/sorting the data or histogram bin count) are disabled in the UI when the asset is not interactive, because they require re-running computations on the original data which is not available. Others (e.g. font size) can always be available because they only require client-side updates.

For assets supporting both (mostly dataviz assets), interaction can be toggled in the constructor via the `interactive=True` param.

## Data storage

Datasets stored for interactive assets can take lots of disk space if not managed properly, and deduplication is important to keep this footprint as low as possible. If 10 panels use the same DataFrame, there should be only a single copy persisted to disk, referenced by each asset.

Asset-backing datasets are stored in the same hash-based object store as artifacts, to ensure further deduplication. If a pushed artifact is also used to back an interactive asset, both should point to the same file on disk.

## Server-side data preparation

Dataset-backed assets may have very simple outputs (e.g. histograms) that is based on a very large dataset under the hood. It is not feasible to render them entirely client-side, since it would require transmitting very large datasets and processing them directly in the browser.

These panels will need to work on a condensed version of the data, delegating most dataset operations to the backend (filtering, aggregation, pagination, ...). This prepared data should rarely exceed a few hundred kilobytes per request.

Interactive assets can re-compute this prepared view when modifiers are updated, by reprocessing the stored dataset. Non-interactive assets only store a pre-computed view that cannot be updated.

DataFrame objects are stored by BulletJournal in parquet format, which facilitates high-speed operations (e.g. using Polars LazyFrames).

## Dashboards

There are two options to visualize assets in a BulletJournal project, they both use the same viewer. Dashboards are opened in their dedicated tab, not embedded within the BulletJournal window.

The dashboard is a single scrollable page with all assets organized vertically in a column that takes most of the screen width.

A collapsible sidebar on the left acts as a table of contents. From there, assets can be reorganized or have their visibility toggled in the main section. When clicking on an asset name in this sidebar, the user is taken to the corresponding panel.

Any change performed in the frontend (panel order, modifiers, ...) is sent to the backend to be persisted. When updating modifiers, the API endpoint can send back an updated view of the data to re-render the panel.

### Dashboard block

A new "Dashboard" block type is added to BulletJournal: it can be configured to view assets from multiple notebooks at once, and overrides are automatically persisted.

In the main editor, there is no visible connection from the notebooks to the Dashboard block. These connections are only displayed when the dashboard is selected.

### Standalone viewer

Each notebook has a "View assets" action which opens a temporary dashboard. When closed, all changes are lost (after prompting for confirmation).

The standalone viewer has a button "Save as dashboard" that persists it to a Dashboard block in the BulletJournal project, and the new block is placed a few spaces above its notebook.

## Persistence

- Asset definitions are persisted in the state database.
- Backing datasets are stored in the artifact object store in `artifacts/objects` and tracked in `metadata/state.db`.
- Dashboard data (order, visibility, modifiers, ...) is stored for each dashboard in `dashboards/<dashboard_id>.json`. The corresponding nodes and positions are stored like all other nodes in `graph/nodes.json` and `graph/layout.json`.
- Dashboard edges (edges from notebooks to a dashboard) are stored in each dashboard's definition (e.g. in `dashboards/<dashboard_id>.json`).

## Handling updates

When an asset is changed externally (i.e. the notebook pushed a new version or the asset is marked stale), the frontend refreshes the corresponding panel(s). The dashboard viewer polls the API every 30 seconds to check for changes in the assets, and refreshes them when changed.

Dashboard-side overrides are preserved when an asset is changed. This can be an issue, especially if the asset changed significantly (e.g. columns were removed/renamed, asset type changed, ...). In this case, the panel explicitly shows an error message and offers a button "Reset panel overrides" that will discard all dashboard-defined overrides.

When multiple users are editing a dashboard, this may result in concurrent/conflicting modifications. This is handled like graph modifications, where the frontend must provide a "dashboard version" value when sending modifications, which is rejected if the stored value is more recent.

## Dataviz assets

### Implementation

Dataviz assets will be mostly based on the Vega-Lite syntax and use vega-embed for rendering (which is available as a React module).

Under the main Vega element, interactive dataviz panels also show the underlying DataFrame in an interactive viewer (using the same UI component as the DataFrame asset type). When modifiers (filter, sort, sampling, ...) are applied to this DataFrame, it can also trigger a recomputation of the dataviz on the server side.

The API provides two separate backend-rendered condensed datasets: one for the Vega dataviz, one for the dataframe/table viewer.

### Modifiers

Dataviz assets can have their modifiers changed in three ways:
- A "settings" menu can be opened by clicking a button in the panel's header: they represent basic settings for the panel (e.g. font size, line width, ...). Each asset type defines its own modifiers appearing in this menu, by exposing a list of type, modifier name/id and title. Types can be: numeric value (float/int), True/False toggle, or enum.
- Modifiers can also be added directly from the DataFrame viewer, e.g. when adding filters.
- There can also be event listeners on vega-embed signals, e.g. when selecting a range on one axis, or clicking the legend to toggle one or more categories. They are implemented in the React component.

Some modifiers can be handled purely client-side, others will require fetching an updated view from the backend.

Each modifier is aware of which server-rendered objects is affected by each modifier (it can be either the view or the table, or none, or both). While the operation is running in the backend, the affected element(s) show visually that the data is being refreshed.

### Filters

There are two ways of filtering the data:
- Apply a filter in the DataFrame viewer
- Selecting directly in the dataviz (e.g. highlighting a time range or clicking a category in the legend)

Modifiers applied from the DataFrame viewer should filter the data and recompute the dataviz.

However, operations coming from the dataviz viewer should only filter the DataFrame, not the dataset used in the Vega panel: for example, selecting one color in a stacked bar chart only shows rows corresponding to that color in the DataFrame, but the dataviz still shows the same data, only applying a visual change to reflect what is selected (e.g. non-selected elements are muted/desaturated).

When the dataviz has its data refreshed by the server, these Vega-originated temporary modifiers are reset.

Similarly, the pagination cursor is reset whenever the DataFrame data is refreshed (unless when it's just changing the page number obviously)

### Dataviz asset types

This section will list panel types and a few useful modifiers. The list of modifiers is not exhaustive.

Common modifiers include:
- Show title
- Show X axis
- Show Y axis
- For assets with multiple series/categories, toggle "show legend"
- For assets with multiple series/categories, allow highlighting one category by clicking it in the legend

#### Histogram

Modifiers:
- Range selection on the X axis in the Vega-lite panel
- Histogram bin count
- Bar size, border thickness
- Y axis type: Linear/Log
- Toggle to show a density overlay + select kernel width + select transparency

#### Timeseries histogram

Data binned by datetime

Modifiers:
- Range selection on the X axis in the Vega-lite panel
- Granularity: Year/Month/Day/Hour/Auto (Auto = largest unit that makes at least 10 bins)

#### Pie chart

Modifiers:
- Highlight a category by clicking on it
- Label threshold % (Only show the label if the category represents >=x%)
- Display threshold % (Only show the pie slice if the category represents >=x%)
- Group small into "others" (All slices not meeting the display threshold % are grouped together in a Others slice) - defaults to True
- Show the pie as a ring (middle of the circle is empty) -> set the ratio
- Set the origin angle of the pie

#### Bar chart

Same as pie chart + allow horizontal/vertical orientation

#### Line plot

Modifiers:
- Line thickness

#### 2D Scatter plot

- Filter X/Y range by dragging a rectangle on the viz
- X and Y axis types : Linear/Log

#### Stacked/multi-series assets

Most dataviz types mentioned above can also show multiple groups on the same plot, either by having a "category" column in the dataframe, or by passing multiple columns to the X and/or Y axis input.

A "color" parameter can also be passed, to set the color scheme.

Implementation details are not provided here, but some additional modifiers may be useful for these plots, like choosing between stacking bars or placing them side by side.

These asset types are not a separate type vs their mono-group counterparts: these assets can natively support a single or multiple groups.

## Asset types - others

## DataFrame

This asset type is pretty much the same as an interactive dataviz asset, without the dataviz. It uses the same viewer component.

Depending on their type, columns can be sorted, filtered, ...

Filter types:
- Numeric range or date range (either or both ends provided)
- Value (checkboxes are suggested for the top 30 most common values + others + empty)
- Regex

There is also an option to take a random sample of N rows.

The number of rows and columns is displayed on the bottom left.

All modifiers can be reset at once with a button

The viewer is paginated, with a default page size of 10 (options are 5/10/20/50/100). Neighboring pages can be pre-loaded from the server to improve performance.

### Markdown

Support for simple Markdown.

### HTML

Support for custom HTML. This has security implications, but the risk model considers that all users are trusted.

### Collections

As shown in the overview, assets can also be a collection of other assets. This can be useful to group several related assets into one.

Collections cannot contain other collections.

Several display options are offered:
- All (show all children)
- Single (only show the first asset, navigation is possible by clicking left/right buttons or select from a dropdown)

## Freshness tracking

Asset registration in the code (`assets.push(...)`) is bound to the same constraints as artifact registration: only top-level declarations, no loops/conditionals, etc.

Asset object instantiation is less constrained, and can for example be in a loop to generate collections with a variable number of assets.

`assets.push` has an optional `asset_type` parameter which is similar to the `data_type` of artifacts. By default, if not specified, the asset type is considered to be a generic Asset class (parent of all asset types).

This weak constraint means that BulletJournal can determine precisely which assets are expected from each notebook by only parsing the source code, but it may not know the type of each asset until runtime when `assets.push(...)` is called. Collections are no different, and the exact number, types and names of their children artifacts are not known until runtime.

This means that it's possible to deduce from the source code the exact list of assets that are expected from each notebook, and reflect these "pending" assets in the dashboard. Asset freshness is tracked in a similar way to artifacts: when the upstream graph/notebooks/data changes, the asset is marked as stale. Assets can also be computed on stale input artifacts, which will mark them as stale immediately.

There is no freshness tracking for children of collection assets: tracking is only performed at the parent collection level, and the entire collection is marked as stale as soon as any of its children is stale.

## UI choices

Do not overcomplicate the UI: keep simple layouts with sober styling, interfaces must be useful and compact.

# MVP scope

The MVP of this project should focus on the following features:
- Asset registration
- Two panel types: Markdown and DataFrame
- Basic DataFrame viewer, only pagination and column sorting, using Polars LazyFrames in the backend. Focus on reusability because this component will be used in other asset panels.
- Unit tests for all implemented features
- No dashboard persistence and no Dashboard block for now, just the single-notebook standalone dashboard
- No sidebar
- No freshness tracking
