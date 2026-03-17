import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _():
    duplicated = artifacts.pull(name='value', data_type=int, default=1)
    return duplicated


@app.cell
def _():
    duplicated = 2
    return duplicated


if __name__ == '__main__':
    app.run()
