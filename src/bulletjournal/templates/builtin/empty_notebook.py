import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='{{NODE_ID}}')

with app.setup:
    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts


@app.cell
def _():
    mo.md("""
    # Custom BulletJournal notebook

    Generic notebook to implement custom data processing.
    """)
    return


@app.cell
def _():
    # Create an input:
    # sample_count = artifacts.pull(name='sample_count', data_type=int, default=10)
    return


@app.cell
def _():
    # Create an output:
    # frame = pd.DataFrame({'value': list(range(sample_count))})
    # artifacts.push(frame, name='sample_df', data_type=pd.DataFrame, description='Sample output frame')
    return


if __name__ == '__main__':
    app.run()
