import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='Example 4')

with app.setup:
    from bulletjournal.runtime import artifacts
    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd


@app.cell
def _(mo):
    mo.md('''
    # Example 4

    Build a markdown summary and a matplotlib visualization from the enriched iris dataset and its derived metrics.
    ''')
    return


@app.cell
def _(pd):
    iris_with_size_category = artifacts.pull(
        name='iris_with_size_category',
        data_type=pd.DataFrame,
        description='Connect Example 2 iris_with_size_category.',
    )
    avg_petal_length = artifacts.pull(
        name='avg_petal_length',
        data_type=float,
        description='Connect Example 3 avg_petal_length.',
    )
    size_category_counts = artifacts.pull(
        name='size_category_counts',
        data_type=dict,
        description='Connect Example 3 size_category_counts.',
    )
    row_count_ok = artifacts.pull(
        name='row_count_ok',
        data_type=bool,
        description='Connect Example 3 row_count_ok.',
    )
    return avg_petal_length, iris_with_size_category, row_count_ok, size_category_counts


@app.cell
def _(avg_petal_length, iris_with_size_category, row_count_ok, size_category_counts):
    species_counts = {
        str(name): int(count)
        for name, count in iris_with_size_category['species'].value_counts().sort_index().items()
    }
    size_lines = '\n'.join(
        f'- `{label}`: {int(size_category_counts.get(label, 0))}'
        for label in ['S', 'M', 'L', 'XL']
    )
    species_lines = '\n'.join(
        f'- `{species}`: {count}'
        for species, count in species_counts.items()
    )
    dataset_summary_markdown = f'''# Iris Dataset Summary

- Rows: {int(iris_with_size_category.shape[0])}
- Columns: {', '.join(map(str, iris_with_size_category.columns))}
- Average petal length: {avg_petal_length:.3f}
- Row count matches expectation: {'yes' if row_count_ok else 'no'}

## Size Categories
{size_lines}

## Species Distribution
{species_lines}
'''

    artifacts.push(
        dataset_summary_markdown,
        name='dataset_summary_markdown',
        data_type=str,
        description='Markdown summary of the enriched iris dataset.',
    )
    return dataset_summary_markdown


@app.cell
def _(avg_petal_length, iris_with_size_category, plt, size_category_counts):
    category_order = ['S', 'M', 'L', 'XL']
    palette = {
        'S': '#4c78a8',
        'M': '#72b7b2',
        'L': '#f2cf5b',
        'XL': '#e45756',
    }

    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    for label in category_order:
        subset = iris_with_size_category[iris_with_size_category['size_category'] == label]
        if subset.empty:
            continue
        ax.scatter(
            subset['sepal_length'],
            subset['petal_length'],
            s=42,
            alpha=0.8,
            label=f"{label} ({int(size_category_counts.get(label, 0))})",
            color=palette[label],
        )

    ax.axhline(avg_petal_length, color='#444444', linestyle='--', linewidth=1.2, label=f'avg petal length = {avg_petal_length:.2f}')
    ax.set_title('Iris measurements grouped by derived size category')
    ax.set_xlabel('Sepal length')
    ax.set_ylabel('Petal length')
    ax.legend(loc='best', frameon=False)
    fig.tight_layout()

    with artifacts.push_file(
        name='iris_size_category_plot',
        extension='.png',
        description='Matplotlib scatter plot of iris measurements by size category.',
    ) as output_path:
        fig.savefig(output_path, dpi=160, bbox_inches='tight')

    plt.close(fig)
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
