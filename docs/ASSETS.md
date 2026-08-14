# Assets

Assets are notebook results that BulletJournal can show as tables, charts, formatted text, embedded pages, or groups of related results. Unlike artifacts, which pass data between notebook blocks, assets are made for viewing. A notebook can produce both artifacts and assets.

## Declaring an asset in a notebook

Import `assets` from the BulletJournal runtime. Assets are imported by default in the notebook setup cell:

```python
from bulletjournal.runtime import assets
```

Create an asset, then publish it with `assets.push(...)`:

```python
summary = assets.Markdown('## Run complete\n\nProcessed 1,250 rows.')

assets.push(
    summary,
    name='run_summary',
    title='Run summary',
    description='A short summary of the latest run.',
    asset_type=assets.Markdown,
)
```

`assets.push(...)` accepts:

- `asset`: the asset to publish.
- `name`: the asset's internal name. Use only lowercase letters, digits, and underscores. The name must be a literal string in the notebook.
- `title`: the title shown to users. It must be a literal string.
- `description`: optional supporting text. It must be a literal string or `None`.
- `asset_type`: optional asset class, such as `assets.Markdown` or `assets.Histogram`. Declaring it lets BulletJournal know the expected type before the notebook runs.

Like artifacts, the `assets.push(...)` call must be a direct statement in a notebook cell. Do not hide it inside a loop, condition, helper function, or alias. The code that builds the asset can use normal Python.

```python
# Supported
chart = assets.Histogram(frame, x='duration')
assets.push(chart, name='duration_histogram', title='Duration', asset_type=assets.Histogram)

# Not supported as an asset declaration
if show_chart:
    assets.push(chart, name='duration_histogram', title='Duration', asset_type=assets.Histogram)
```

## Dashboards and modifiers

Assets can be added to dashboards. A dashboard panel points to an asset produced by a notebook, so the dashboard updates when the notebook produces a new version of that asset.

Modifiers change how an asset is shown. For example, they can change chart labels, bar width, table filters, or the number of histogram bins. Chart modifiers passed to an asset constructor become that asset's defaults:

```python
chart = assets.Histogram(
    frame,
    x='duration',
    bin_count=30,
    bar_width=80,
    title={'hide_title': False, 'text': 'Movie duration'},
)
```

Interactive assets also have controls in their asset panel and dashboard panel. Dashboard panel changes are saved for that panel and override the notebook defaults without changing the notebook. Changes on a notebook's assets page are included when that page is saved as a dashboard. Resetting a panel returns it to the notebook defaults.

Paging, sorting, and filtering are panel modifiers. They cannot be passed to an asset constructor. Chart clicks, selected slices, selected points, and selected ranges are temporary interactions rather than saved modifiers.

## Shared interactive modifiers

`DataFrame`, `BarChart`, `Histogram`, `PieChart`, and `ScatterPlot` all show a table and accept the following modifiers in their asset or dashboard panel.

### `page`

Chooses the table page and the number of rows shown on each page.

```python
{'index': 0, 'size': 25}
```

- `index`: zero-based page number. It must be `0` or greater.
- `size`: rows per page. Accepted values are `10`, `25`, `50`, and `100`.

The default page size is `25` for `DataFrame` and `10` for chart assets.

### `sort`

Sorts the table by one column. Only one active sort is supported.

```python
[{'column': 'duration', 'direction': 'desc'}]
```

- `column`: an existing column name.
- `direction`: `asc` or `desc`.

Use an empty list to remove sorting. Sorting changes the table order, not the order of bars, slices, or other chart values.

### `filters`

Filters rows before they are shown. Chart filters affect both the chart and its table. A `DataFrame` filter affects its table. Only one filter per column can be active at a time.

A range filter accepts a lower bound, an upper bound, or both. Use it with numbers, dates, and times:

```python
[
    {'kind': 'range', 'column': 'duration', 'lower': 60, 'upper': 180},
]
```

An exact-value filter keeps listed values and can optionally include empty values:

```python
[
    {
        'kind': 'value',
        'column': 'genre',
        'values': ['Drama', 'Comedy'],
        'include_null': False,
    },
]
```

A regular-expression filter matches text:

```python
[
    {
        'kind': 'regex',
        'column': 'title',
        'pattern': '^The ',
        'case_sensitive': False,
    },
]
```

Use ISO-formatted strings for date and time bounds: `2026-08-14` for a date, `2026-08-14T09:30:00` for a datetime, or `09:30:00` for a time.

## Shared chart modifiers

Bar charts, histograms, and scatter plots use the same shape for axis modifiers. Each field is optional, so a notebook can set only the fields it needs.

```python
x_axis={
    'label_size': 12,
    'label': 'Duration in minutes',
    'hide_label': False,
    'tick_count': 10,
    'tick_size': 11,
    'show_grid_lines': True,
    'scale': 'lin',
}
```

- `label_size`: axis label size, or `None` to use the chart default.
- `label`: axis label text.
- `hide_label`: hides the axis label when `True`.
- `tick_count`: requested number of tick marks, or `None` for automatic ticks.
- `tick_size`: tick label size, or `None` to use the chart default.
- `show_grid_lines`: shows grid lines when `True`.
- `scale`: `lin` for a linear scale or `log` for a logarithmic scale. Logarithmic scaling applies to numeric axes with positive values. A bar chart's category axis and a date histogram's time axis remain non-logarithmic.

Chart title modifiers also use a shared shape:

```python
title={
    'size': 16,
    'text': 'Movie duration',
    'hide_title': False,
    'position': 'top',
}
```

- `size`: title size, or `None` to use the chart default.
- `text`: title text.
- `hide_title`: hides the title when `True`. Chart titles are hidden by default.
- `position`: `top` or `bottom`.

## Markdown

`Markdown` shows formatted Markdown text. It is useful for summaries, instructions, warnings, and short reports.

```python
assets.Markdown(text)
```

### Options and modifiers

- `text`: Markdown content as a string.
- Modifiers: none.

### Example

```python
report = assets.Markdown(
    '## Quality check\n\n'
    '- **Rows checked:** 1,250\n'
    '- **Missing values:** 3\n'
    '- Status: ready for review'
)

assets.push(
    report,
    name='quality_report',
    title='Quality report',
    description='Results of the data quality checks.',
    asset_type=assets.Markdown,
)
```

This creates a formatted report with a heading, a list, and bold values. Markdown has no display modifiers because its formatting comes from the text itself.

## Iframe

`Iframe` embeds a web page in the asset panel. The target page must allow itself to be displayed inside an iframe.

```python
assets.Iframe(url)
```

### Options and modifiers

- `url`: the page URL as a string.
- Modifiers: none.

### Example

```python
map_asset = assets.Iframe('https://example.com/public-map')

assets.push(
    map_asset,
    name='public_map',
    title='Public map',
    description='The published map for this dataset.',
    asset_type=assets.Iframe,
)
```

This embeds the public map in BulletJournal. If the page blocks iframe use, the browser will not display it; use a link or another asset type instead.

## Collection

`Collection` puts several assets in one panel. Use it when a set of tables, charts, or notes belongs together. A collection cannot contain another collection.

```python
assets.Collection(display_mode='single')
```

Add children with:

```python
collection.add_asset(asset, name='child_name', title='Child title')
```

`add_asset(...)` can be called in a loop. The restriction on loops applies to the final `assets.push(...)` declaration, not to building a collection.

### Options and modifiers

- `display_mode`: initial display mode. `single` shows one child at a time; `all` shows every child. The default is `single`.
- Child `name`: optional unique name. When omitted, BulletJournal uses `asset_1`, `asset_2`, and so on.
- Child `title`: optional display title. When omitted, BulletJournal uses `Asset 1`, `Asset 2`, and so on.
- `selected_child`: panel setting that chooses the visible child in `single` mode.
- Child modifiers: each child keeps the modifiers supported by its own asset type. Dashboard panels also save each child's view settings and chart height.

The panel can switch between `single` and `all` while it is open. Currently, a saved `all` panel setting is not restored when the collection's constructor default is `single`; set `display_mode='all'` in the notebook when the collection should always open in that mode.

### Example

```python
overview = assets.Collection(display_mode='all')
overview.add_asset(
    assets.Markdown('## Sales review\n\nResults for the current quarter.'),
    name='notes',
    title='Review notes',
)

for region in sales['region'].dropna().unique():
    region_sales = sales[sales['region'] == region]
    overview.add_asset(
        assets.BarChart(
            region_sales,
            category='product',
            value='revenue',
            aggregation='sum',
            bar_width=75,
            title={'hide_title': False, 'text': f'Revenue in {region}'},
        ),
        name=f'{region.lower()}_chart',
        title=f'{region} revenue',
    )

assets.push(
    overview,
    name='sales_overview',
    title='Sales overview',
    asset_type=assets.Collection,
)
```

This adds the notes first, then uses a loop to create one product-revenue chart for each region. The collection uses `all` mode, each child chart keeps its own defaults, and the collection itself is declared once after the loop.

## DataFrame

`DataFrame` shows a pandas DataFrame as an interactive table.

```python
assets.DataFrame(dataframe)
```

### Options and modifiers

- `dataframe`: a `pandas.DataFrame`.
- `page`: table page and page size. Default: `{'index': 0, 'size': 25}`.
- `sort`: one table sort. Default: `[]`.
- `filters`: table filters. Default: `[]`.

`page`, `sort`, and `filters` are changed in the asset or dashboard panel; they are not constructor arguments.

### Example

```python
table = assets.DataFrame(
    movies[['title', 'genre', 'release_date', 'duration']]
)

assets.push(
    table,
    name='movie_table',
    title='Movies',
    description='A table that can be paged, sorted, and filtered.',
    asset_type=assets.DataFrame,
)
```

This publishes four columns as a table. A user can filter `genre`, sort by `release_date`, and choose a page size in the panel; those choices can be saved separately in each dashboard panel.

## Histogram

`Histogram` groups numeric values into ranges, or dates into time periods, and shows how many rows fall into each group.

```python
assets.Histogram(
    dataframe,
    *,
    x,
    bin_count=None,
    granularity='auto',
    **modifiers,
)
```

### Data options

- `dataframe`: a `pandas.DataFrame`.
- `x`: the numeric, date, or datetime column to count.
- `bin_count`: number of ranges for a numeric column. The default is `20`; accepted panel values are `1` through `100`.
- `granularity`: grouping for a date or datetime column. Accepted values are `auto`, `year`, `month`, `week`, `day`, and `hour`. Date-only columns do not support `hour`.

Use `bin_count`, not `bins`. Histograms do not accept `color`, `shape`, or `size` columns.

### Modifiers

- `bar_width`: bar width as a percentage from `0` to `100`. Default: `90`.
- `border_thickness`: bar border width. Use `0` for no border. Default: `0`.
- `x_axis`: shared axis modifier object. The default label is the `x` column.
- `y_axis`: shared axis modifier object. The default label is `Rows`.
- `title`: shared chart title modifier object.
- `bin_count`: saved numeric-histogram setting, from `1` to `100`.
- `granularity`: saved date-histogram setting.
- `page`, `sort`, and `filters`: shared interactive modifiers.

`bar_width`, `border_thickness`, `x_axis`, `y_axis`, and `title` can be passed to the constructor. `bin_count` and `granularity` have their own named constructor arguments. Paging, sorting, and filtering are panel settings.

### Example

```python
duration_histogram = assets.Histogram(
    movies,
    x='duration',
    bin_count=30,
    bar_width=82,
    border_thickness=1,
    x_axis={'label': 'Duration in minutes', 'tick_count': 8},
    y_axis={'label': 'Number of movies', 'show_grid_lines': False},
    title={'hide_title': False, 'text': 'Movie duration'},
)

assets.push(
    duration_histogram,
    name='duration_histogram',
    title='Movie duration',
    asset_type=assets.Histogram,
)
```

This splits movie durations into 30 ranges, makes the bars slightly narrower, adds a thin border, changes both axis labels, and shows the chart title. Dragging across bars temporarily filters the table to the selected duration range.

## BarChart

`BarChart` groups rows by a category and calculates a value for each category. It can also split each category into groups.

```python
assets.BarChart(
    dataframe,
    *,
    category,
    value,
    aggregation='sum',
    group=None,
    color=None,
    **modifiers,
)
```

### Data options

- `dataframe`: a `pandas.DataFrame`.
- `category`: column used along the x-axis.
- `value`: column used for the calculation.
- `aggregation`: calculation to perform. Accepted values are `sum`, `mean`, `avg`, `average`, `count`, `len`, `size`, `unique`, `nunique`, `min`, `max`, and `median`. `avg` and `average` mean `mean`; `len` and `size` mean `count`; `nunique` means `unique`.
- `group`: optional column that splits each category into multiple bars or stacked sections.
- `color`: optional color column or dictionary. Without `group`, a color column or dictionary maps category values to colors. With `group`, use a dictionary whose keys match group values; grouped color columns are not currently supported. Color strings can be CSS colors such as `#3568d4` or `tomato`.

`sum`, `mean`, `min`, `max`, and `median` require a numeric `value` column.

### Modifiers

- `bar_width`: bar width as a percentage from `0` to `100`. Default: `90`.
- `border_thickness`: bar border width. Default: `0`.
- `category_order`: order of categories. Default: `category_asc`.
- `group_order`: accepted group-order setting. Default: `category_asc`. It is currently stored but does not change the rendered group order.
- `group_mode`: `grouped` or `stacked`. Default: `grouped`.
- `group_normalize`: when `group_mode='stacked'`, `True` shows each category as parts of a whole. Default: `False`.
- `group_spacing`: space between grouped bars, from `0` to `50`. Default: `10`.
- `x_axis`: shared axis modifier object. The default label is the category column.
- `y_axis`: shared axis modifier object. Its default label describes the calculation.
- `title`: shared chart title modifier object.
- `page`, `sort`, and `filters`: shared interactive modifiers.

`category_order` and `group_order` accept one of these values:

- `category_asc`: category values in ascending order.
- `category_desc`: category values in descending order.
- `value_asc`: calculated values from smallest to largest.
- `value_desc`: calculated values from largest to smallest.
- A list such as `['North', 'South', 'West', 'East']`: puts listed values first in that order.

All chart modifiers in this section can be passed to the constructor. The current panel does not include controls for `category_order` or `group_order`, so set them in the constructor. Paging, sorting, and filtering are panel settings.

### Example

```python
regional_sales = assets.BarChart(
    sales,
    category='region',
    value='revenue',
    aggregation='sum',
    group='channel',
    color={'Online': '#3568d4', 'Store': '#e07a3f'},
    category_order='value_desc',
    group_mode='stacked',
    group_spacing=4,
    x_axis={'label': 'Sales region'},
    y_axis={'label': 'Revenue', 'show_grid_lines': True},
    title={'hide_title': False, 'text': 'Revenue by region and channel'},
)

assets.push(
    regional_sales,
    name='regional_sales',
    title='Regional sales',
    asset_type=assets.BarChart,
)
```

This totals revenue by region, splits each total by sales channel, stacks the channel values, applies fixed colors, and orders regions by total revenue. Clicking a bar or legend value temporarily filters the table.

## PieChart

`PieChart` counts rows by category and shows each category's share of the total.

```python
assets.PieChart(
    dataframe,
    *,
    category,
    color=None,
    **modifiers,
)
```

### Data options

- `dataframe`: a `pandas.DataFrame`.
- `category`: column used to create slices.
- `color`: optional color column or dictionary whose keys match category values. A color column must give each category one non-empty color string. A dictionary can include only selected categories; categories not in it use a fallback color.

### Modifiers

- `inner_radius`: size of the center hole, from `0` for a full pie to `1` for the maximum hole. Default: `0.5`.
- `label_size`: slice label size. It must be at least `1`. Default: `20`.
- `label_threshold`: hides labels for slices smaller than this percentage, from `0` to `100`. Default: `5`.
- `label_position`: label distance from the center as a percentage of the outer radius, from `0` to `200`. Default: `102`.
- `merge_threshold`: treats slices smaller than this percentage as small categories, from `0` to `100`. Default: `0`.
- `border_thickness`: slice border width. It must be `0` or greater. Default: `3`.
- `category_order`: category ordering mode or an explicit list, using the same values as `BarChart`. Default: `value_desc`.
- `merged_category_label`: label for the combined small-category slice. Default: `Others`.
- `show_merged_category`: combines small categories into one slice when `True`; hides them when `False`. Default: `True`.
- `show_percentages`: adds percentages to labels when `True`. Default: `False`.
- `title`: shared chart title modifier object.
- `page`, `sort`, and `filters`: shared interactive modifiers.

All chart modifiers in this section can be passed to the constructor. The current panel does not include a control for `category_order`, so set it in the constructor. Paging, sorting, and filtering are panel settings.

### Example

```python
genre_share = assets.PieChart(
    movies,
    category='genre',
    color={
        'Drama': '#5b5f97',
        'Comedy': '#ffc145',
        'Action': '#ff6b6c',
    },
    inner_radius=0.62,
    merge_threshold=3,
    merged_category_label='Other genres',
    show_percentages=True,
    label_position=90,
    title={'hide_title': False, 'text': 'Movies by genre'},
)

assets.push(
    genre_share,
    name='genre_share',
    title='Genre share',
    asset_type=assets.PieChart,
)
```

This creates a donut chart, combines genres below three percent into `Other genres`, shows percentages, moves labels inward, and uses fixed colors for selected genres. Clicking a slice temporarily filters the table to that genre.

## ScatterPlot

`ScatterPlot` compares two numeric columns. Optional columns can add point labels, shapes, sizes, and colors. A scatter plot supports at most 10,000 rows.

```python
assets.ScatterPlot(
    dataframe,
    *,
    x,
    y,
    label=None,
    shape=None,
    size=None,
    color=None,
    size_scaling=1,
    **modifiers,
)
```

### Data options

- `dataframe`: a `pandas.DataFrame` with no more than 10,000 rows.
- `x`: numeric column used on the x-axis.
- `y`: numeric column used on the y-axis.
- `label`: optional column shown in point details.
- `shape`: optional column that chooses point shapes.
- `size`: optional column that chooses point sizes. It can contain numbers or categories.
- `color`: optional column that chooses point colors. It can contain numbers or categories.

### Modifiers

- `min_point_size`: smallest point size when a `size` column is used. Default: `50`.
- `max_point_size`: largest point size when a `size` column is used. Default: `250`.
- `size_scaling`: how strongly numeric size differences are shown, from `0.1` to `3.0`. Default: `1`.
- `show_legend`: shows shape, size, and color legends when `True`. Default: `True`.
- `shape_style`: `outline` or `filled`. Default: `outline`.
- `x_axis`: shared axis modifier object. The default label is the `x` column.
- `y_axis`: shared axis modifier object. The default label is the `y` column.
- `title`: shared chart title modifier object.
- `page`, `sort`, and `filters`: shared interactive modifiers.

All chart modifiers in this section can be passed to the constructor. `size_scaling` can be passed as the named argument shown in the signature. Paging, sorting, and filtering are panel settings.

### Example

```python
price_vs_area = assets.ScatterPlot(
    homes,
    x='floor_area',
    y='price',
    label='address',
    shape='property_type',
    size='bedrooms',
    color='neighborhood',
    min_point_size=60,
    max_point_size=320,
    size_scaling=1.4,
    shape_style='filled',
    x_axis={'label': 'Floor area (m2)', 'show_grid_lines': False},
    y_axis={'label': 'Price', 'tick_count': 6},
    title={'hide_title': False, 'text': 'Price compared with floor area'},
)

assets.push(
    price_vs_area,
    name='price_vs_area',
    title='Price and floor area',
    asset_type=assets.ScatterPlot,
)
```

This plots floor area against price, uses the address in point details, varies shape by property type, sizes points by bedroom count, and colors them by neighborhood. Dragging over points, clicking a point, or selecting a categorical color, shape, or size legend value temporarily filters the table.
