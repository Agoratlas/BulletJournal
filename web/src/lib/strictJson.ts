export function parseStrictJson(text: string): unknown {
  const value: unknown = JSON.parse(text)
  const tokens = text.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ?? []
  const scopes: Array<Set<string> | null> = []
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]
    if (token === '{') scopes.push(new Set())
    else if (token === '[') scopes.push(null)
    else if (token === '}' || token === ']') scopes.pop()
    else if (token.startsWith('"') && tokens[index + 1] === ':') {
      const keys = scopes[scopes.length - 1]
      const key = JSON.parse(token) as string
      if (keys?.has(key)) throw new Error(`Duplicate JSON object key: ${key}.`)
      keys?.add(key)
    }
  }
  return value
}
