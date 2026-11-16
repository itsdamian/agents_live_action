// Minimal typing for the only Node global the config files use. This avoids adding
// @types/node, which is outside the dependency list approved in plan §13.
declare const process: { env: Record<string, string | undefined> };
