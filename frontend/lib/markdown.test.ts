import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
    it("renders paragraphs, emphasis, code and lists", () => {
        expect(renderMarkdown("Hello **you**,\nthis is *it* with `code`.\n\n- a\n- b\n\n1. one")).toBe(
            "<p>Hello <strong>you</strong>,<br>this is <em>it</em> with <code>code</code>.</p><ul><li>a</li><li>b</li></ul><ol><li>one</li></ol>",
        );
    });

    it("escapes the model's text before adding markup", () => {
        expect(renderMarkdown('<script>alert("x")</script> & **<b>**')).toBe(
            "<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; <strong>&lt;b&gt;</strong></p>",
        );
    });

    it("rewrites the model's citation markers to [n]", () => {
        expect(renderMarkdown("Revenue grew 【2†L10-L12】 and 【3】.")).toBe("<p>Revenue grew [2] and [3].</p>");
    });

    it("renders a fenced table as a table, right-aligning figures and coloring signs", () => {
        const html = renderMarkdown(
            ["```markdown", "| Symbol | Qty | P&L % |", "|---|---|:--|", "| AAPL | 10 | +81.0% |", "| NVDA | 1,200 | -3.5% |", "```"].join("\n"),
        );
        expect(html).toContain('<div class="table-wrap"><table>');
        expect(html).toContain('<th style="text-align:left">Symbol</th><th style="text-align:right">Qty</th><th style="text-align:left">P&amp;L %</th>');
        expect(html).toContain('<td class="gain" style="text-align:left">+81.0%</td>');
        expect(html).toContain('<td class="loss" style="text-align:left">-3.5%</td>');
        expect(html).not.toContain("```");
    });
});
