using System;
using System.Collections.Generic;
using System.Linq;

namespace LlmKnowledgeBase.Revit.Core;

/// <summary>Strict, Autodesk-independent input validation for the separate PDF diagnostic command.</summary>
public sealed class PdfDiagnosticRequest
{
    public IReadOnlyList<string> SheetNumbers { get; }
    public string Mode { get; }
    public string? SetupName { get; }

    private PdfDiagnosticRequest(string[] sheetNumbers, string mode, string? setupName)
    {
        SheetNumbers = Array.AsReadOnly(sheetNumbers);
        Mode = mode;
        SetupName = setupName;
    }

    public static PdfDiagnosticRequest Parse(string? sheetNumbers, string? mode, string? setupName)
    {
        if (string.IsNullOrWhiteSpace(sheetNumbers))
            throw new ArgumentException("Set LLM_KB_PDF_DIAG_SHEET_NUMBERS to one or two exact Sheet numbers.");
        var numbers = sheetNumbers.Split(',').Select(number => number.Trim()).ToArray();
        if (numbers.Length > 2 || numbers.Any(string.IsNullOrWhiteSpace)
            || numbers.Distinct(StringComparer.Ordinal).Count() != numbers.Length)
            throw new ArgumentException("PDF diagnostics require one or two distinct, nonempty Sheet numbers.");
        if (mode is not ("default" or "saved"))
            throw new ArgumentException("Set LLM_KB_PDF_DIAG_MODE to exactly 'default' or 'saved'.");
        if (mode == "saved" && string.IsNullOrWhiteSpace(setupName))
            throw new ArgumentException("Saved mode requires LLM_KB_PDF_DIAG_SETUP_NAME.");
        return new(numbers, mode, mode == "saved" ? setupName : null);
    }

    /// <summary>Keep the requested order; never choose a replacement or an ambiguous match.</summary>
    public IReadOnlyList<T> SelectSheets<T>(IEnumerable<T> sheets,
        Func<T, string> number, Func<T, bool> printable)
    {
        var candidates = sheets.ToArray();
        var selected = new List<T>();
        foreach (var requested in SheetNumbers)
        {
            var matches = candidates.Where(sheet => string.Equals(number(sheet), requested,
                StringComparison.Ordinal)).ToArray();
            if (matches.Length != 1)
                throw new InvalidOperationException($"Sheet '{requested}' has {matches.Length} exact matches; expected one.");
            if (!printable(matches[0]))
                throw new InvalidOperationException($"Sheet '{requested}' is a placeholder or cannot be printed.");
            selected.Add(matches[0]);
        }
        return selected.AsReadOnly();
    }
}
