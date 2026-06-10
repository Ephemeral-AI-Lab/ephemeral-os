export class Markdown {
  static join(parts) {
    return parts.filter(part => part !== null && part !== undefined).join("\n");
  }

  static shiftHeadings(markdown) {
    return markdown
      .split("\n")
      .map(line => line.startsWith("#") ? "#" + line : line)
      .join("\n");
  }

  static pendingOr(value) {
    return value && value.trim() ? value : "Pending to Run";
  }
}
