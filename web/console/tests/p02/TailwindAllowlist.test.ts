import { readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

interface TailwindAllowlist {
  classNameExpressions: number;
  dynamicClassNameExpressions: number;
  staticExpressionParts: number;
  utilityTokens: string[];
}

interface ClassNameStats {
  classNameExpressions: number;
  dynamicClassNameExpressions: number;
  staticExpressionParts: number;
  utilityTokens: string[];
}

async function sourceFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return sourceFiles(path);
      return /\.(?:ts|tsx)$/.test(entry.name) ? [path] : [];
    }),
  );
  return files.flat();
}

function addStaticParts(expression: ts.Expression, parts: string[]) {
  if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) {
    parts.push(expression.text);
    return;
  }
  if (ts.isTemplateExpression(expression)) {
    parts.push(expression.head.text, ...expression.templateSpans.map((span) => span.literal.text));
    return;
  }
  if (ts.isConditionalExpression(expression)) {
    addStaticParts(expression.whenTrue, parts);
    addStaticParts(expression.whenFalse, parts);
    return;
  }
  if (ts.isParenthesizedExpression(expression)) {
    addStaticParts(expression.expression, parts);
    return;
  }
  if (ts.isBinaryExpression(expression) && expression.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    addStaticParts(expression.left, parts);
    addStaticParts(expression.right, parts);
    return;
  }
  if (ts.isCallExpression(expression) && ts.isIdentifier(expression.expression) && expression.expression.text === "cn") {
    expression.arguments.forEach((argument) => addStaticParts(argument, parts));
  }
}

async function collectClassNameStats(): Promise<ClassNameStats> {
  const files = await sourceFiles(resolve(process.cwd(), "src"));
  const parts: string[] = [];
  let classNameExpressions = 0;
  let dynamicClassNameExpressions = 0;

  for (const file of files) {
    const source = ts.createSourceFile(file, await readFile(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const visit = (node: ts.Node) => {
      if (ts.isJsxAttribute(node) && node.name.text === "className") {
        classNameExpressions += 1;
        const partsBefore = parts.length;
        if (node.initializer && ts.isStringLiteral(node.initializer)) {
          parts.push(node.initializer.text);
        } else if (node.initializer && ts.isJsxExpression(node.initializer) && node.initializer.expression) {
          addStaticParts(node.initializer.expression, parts);
        }
        if (parts.length === partsBefore) dynamicClassNameExpressions += 1;
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }

  return {
    classNameExpressions,
    dynamicClassNameExpressions,
    staticExpressionParts: parts.length,
    utilityTokens: [...new Set(parts.flatMap((part) => part.split(/\s+/).filter(Boolean)))].sort(),
  };
}

describe("P02 temporary Tailwind allowlist", () => {
  it("is removal-only until P10 deletes the legacy styling layer", async () => {
    const allowlist = JSON.parse(
      await readFile(resolve(process.cwd(), "tests/p02/tailwind-allowlist.json"), "utf8"),
    ) as TailwindAllowlist;
    const actual = await collectClassNameStats();
    const allowedTokens = new Set(allowlist.utilityTokens);
    const introducedTokens = actual.utilityTokens.filter((token) => !allowedTokens.has(token));

    expect(allowlist.utilityTokens).toHaveLength(259);
    expect(actual.classNameExpressions).toBeLessThanOrEqual(allowlist.classNameExpressions);
    expect(actual.staticExpressionParts).toBeLessThanOrEqual(allowlist.staticExpressionParts);
    expect(actual.dynamicClassNameExpressions).toBeLessThanOrEqual(allowlist.dynamicClassNameExpressions);
    expect(introducedTokens).toEqual([]);
  });
});
