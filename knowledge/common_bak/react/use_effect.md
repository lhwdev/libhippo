---
title: "React useEffect"
version: "19.3.0"
source:
  - https://react.dev/reference/react/useEffect
  - https://react.dev/learn/you-might-not-need-an-effect
  - https://react.dev/reference/react/StrictMode
version_check: "npm:react"
tags: ["react", "javascript", "hooks", "effects", "frontend"]
related:
  - common/react.md
importance: 0.75
---
# React `useEffect`

## Purpose and signature

`useEffect(setup, dependencies?)` is a Hook for synchronizing a committed component with an external system not controlled by React: for example, a subscription, timer, browser API, network integration, or imperative third-party widget. It returns `undefined`. Import it from `react` and call it only at the top level of a function component or custom Hook, in a stable order—not in conditions, loops, event handlers, or nested functions.

An Effect is an escape hatch, not a general mechanism for responding to state changes. Calculate values derived from props/state while rendering; handle a known user action in its event handler. Avoid Effects that copy derived data into state or trigger avoidable render cycles. Prefer framework-provided data loading where available.

## Lifecycle and dependencies

`setup` runs after React commits the component. It may return a cleanup function. On a later commit where any dependency changed, React first calls cleanup with the old closure values, then calls setup with the new values. On removal, React calls the last cleanup. Think of each setup/cleanup pair as one independent synchronization process; cleanup should undo or stop what setup established (unsubscribe, clear timer, disconnect).

The optional dependency array must be inline, have a constant length, and include every reactive value read by setup or cleanup: props, state, and variables/functions declared in the component. React compares entries with `Object.is`; the `eslint-plugin-react-hooks` dependency lint is the practical check. Dependency cases:

- Omitted: run after every commit.
- `[]`: run setup after initial commit and cleanup on removal, absent development Strict Mode's extra check.
- `[a, b]`: run initially and when either dependency changes.

Do not silence the dependency linter to force a desired schedule. Instead restructure the code: move non-reactive constants/functions outside the component, create transient objects inside the Effect, or use an appropriate updater when state is based on prior state. Objects/functions created on every render can change identity each time and cause repeated synchronization; avoid unnecessary dependencies rather than relying reflexively on memoization.

```js
useEffect(() => {
  const connection = createConnection(serverUrl, roomId);
  connection.connect();
  return () => connection.disconnect();
}, [serverUrl, roomId]);
```

## Important behavior and pitfalls

Effects run only on the client, not during server rendering. In development, Strict Mode performs an extra setup → cleanup cycle before the first real setup to expose incomplete cleanup; code must remain correct under this stress test. It is not a production double-mount guarantee. Effects usually run after paint when not interaction-caused. For visual work that visibly flickers (such as measuring and positioning a tooltip), use `useLayoutEffect` when work must happen before paint; it blocks painting and should be reserved for that need.

For asynchronous work, prevent obsolete results from updating the UI when inputs change or a component is removed—for example, use an `AbortController` where supported, or a cleanup flag. An `async` setup itself returns a Promise, not a cleanup function: keep setup synchronous and define an inner async task. Treat dependencies as the synchronization inputs, not as a way to emulate class lifecycle methods. Custom Hooks can encapsulate reusable synchronization behavior.

## Troubleshooting

If an Effect reruns unexpectedly, inspect each dependency identity and the linter's reactive-value report. An infinite cycle commonly means the Effect updates state, that update changes a dependency, and setup consequently repeats; reconsider whether the Effect is needed or make the update/dependency logic sound. If cleanup seems to run while mounted, a dependency changed or Strict Mode is checking cleanup in development. Consult the official reference for detailed fetching, latest-value, and dependency-removal patterns.

## Official references

- [`useEffect` API reference](https://react.dev/reference/react/useEffect)
- [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect)
- [`StrictMode`](https://react.dev/reference/react/StrictMode)
