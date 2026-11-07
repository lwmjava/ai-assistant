import { expect, test } from 'vitest'

import { parsePromptVariables } from './workflow-definition'

test('keeps placeholder names in appearance order', () => {
  expect(parsePromptVariables('hello {customer} {day}')).toEqual({
    ok: true,
    variables: ['customer', 'day'],
  })
})

test('rejects identifiers the server would reject', () => {
  expect(parsePromptVariables('hello {1bad}').ok).toBe(false)
  expect(parsePromptVariables('plain').variables).toEqual([])
})
