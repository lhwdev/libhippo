---
title: React
version: "19.3.0"
source:
- https://react.dev/learn
- https://react.dev/reference/react
- https://react.dev/reference/rules
- https://react.dev/learn/you-might-not-need-an-effect
- https://react.dev/reference/rsc/server-components
- https://react.dev/reference/react-dom/client/createRoot
version_check: npm:react
tags:
- javascript
- frontend
- ui
- library
related:
- common/javascript
- common/typescript
- common/web-accessibility
status: active
importance: 0.76
---
# React

React is a JavaScript library for building user interfaces from reusable components. Components describe UI from inputs; React manages rendering and updates as those inputs change. React DOM supports browser interfaces and React Native supports native platforms. React does not prescribe a complete application architecture: frameworks typically provide routing, data loading, build tooling, and server rendering. Check installed `react` and renderer versions and matching framework documentation before relying on version-specific behavior.

## Components and rendering

A function component receives props and returns a React node, commonly expressed with JSX. JSX is JavaScript syntax, not HTML: close tags, use `className` for CSS classes, and put JavaScript expressions in braces. Component names begin with capitals; lowercase JSX names refer to built-in elements. Compose components using JSX (for example, `<Profile />`), rather than calling component functions directly. Use JavaScript conditions and collection methods for lists; give each item a stable, unique `key` from data so React preserves identity when items move, appear, or disappear.

Rendering must be pure: with the same props, state, and context, a component should return the same result without mutating inputs or causing side effects. Props and state are immutable snapshots. React may render more than once or discard work, so do not rely on render as a one-time operation. Put interactions in event handlers and use semantic HTML. React does not automatically provide accessible names, keyboard behavior, or focus management.

## State, context, and Hooks

Props are read-only inputs. State persists between renders; `useState` or `useReducer` supplies state and an update function that schedules a render. Each render sees a snapshot. When a value depends on prior state, use a functional updater. Keep state minimal and calculate derived values during rendering instead of duplicating state. Place shared state in the closest common owner; use context when many descendants need a value. Refs hold DOM references or mutable values that should not trigger rendering.

Hooks (including custom Hooks, conventionally named with a `use` prefix) must be called at the top level of function components or other Hooks, in the same order each render—not inside conditions, loops, nested functions, event handlers, or after an early return. The official `eslint-plugin-react-hooks` catches common violations. Development `StrictMode` can reveal impure rendering and missing effect cleanup; repeated development work is not a production guarantee.

## Effects and external systems

`useEffect` synchronizes React with an external system such as a subscription, browser API, network integration, or non-React widget. List reactive dependencies and return cleanup when setup creates something to stop. Effects are not a general response to state changes: calculate derived data during render and handle user actions in event handlers. Prefer framework data-loading facilities where available. Use `useLayoutEffect` only when work must happen before paint, such as measuring layout.

## Platforms and practical workflow

For a browser client-rendered app, `createRoot` from `react-dom/client` creates a root and `root.render` displays the app. Use `hydrateRoot` for HTML already rendered by React; `createRoot` clears existing root content. Frameworks usually own setup. Server Components execute in a server/build environment and are not shipped to the browser as component code. Interactive UI uses Client Components; `'use client'` marks a client-module boundary in compatible frameworks. Server Components require framework or bundler support.

Measure before optimizing. `useMemo` and `useCallback` are performance tools, not correctness requirements; React Compiler may reduce manual memoization in supported setups. Start with the Learn guide, consult the API reference for exact behavior, and use framework documentation for routing, data fetching, rendering, and deployment.

## Official references

- [Learn React](https://react.dev/learn)
- [API reference](https://react.dev/reference/react)
- [Rules of React](https://react.dev/reference/rules)
- [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect)
- [Server Components](https://react.dev/reference/rsc/server-components)
- [Client root API](https://react.dev/reference/react-dom/client/createRoot)
