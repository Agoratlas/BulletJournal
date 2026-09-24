export function backgroundCategoryGridLayer(gridColor: string, paddingInner: number) {
  return {
    transform: [{ aggregate: [{ op: 'count' as const, as: 'bar_count' }], groupby: ['category_label', 'category_index'] }],
    mark: { type: 'rule' as const, color: gridColor, strokeWidth: 1 },
    encoding: {
      x: {
        field: 'category_label',
        type: 'nominal' as const,
        sort: { field: 'category_index', op: 'min' as const, order: 'ascending' as const },
        scale: { paddingInner, paddingOuter: 0.08 },
      },
    },
  }
}
