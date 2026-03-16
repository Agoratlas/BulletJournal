import marimo

app = marimo.App()

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _():
    p = artifacts.push
    p(1, name='x', data_type=int, is_output=True)
    return


if __name__ == '__main__':
    app.run()
