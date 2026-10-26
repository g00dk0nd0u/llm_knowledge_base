using System;
using System.Linq;
using LlmKnowledgeBase.Revit.Core;
using Xunit;

public sealed class PdfDiagnosticRequestTests
{
    [Fact]
    public void SelectsOnlyExactRequestedSheetsInRequestedOrder()
    {
        var request = PdfDiagnosticRequest.Parse(" G6.001, Admin CD.31 ", "default", "ignored setup");
        var source = new[] { ("Admin CD.31", 31), ("other", 99), ("G6.001", 1) };
        var selected = request.SelectSheets(source, sheet => sheet.Item1, _ => true);

        Assert.Equal(new[] { 1, 31 }, selected.Select(sheet => sheet.Item2));
        Assert.Equal("default", request.Mode);
        Assert.Null(request.SetupName);
        Assert.Equal(3, source.Length);
    }

    [Fact]
    public void AcceptsOneSheetAndPreservesExactSavedSetupName()
    {
        var request = PdfDiagnosticRequest.Parse("G6.001", "saved", " PDF Export Setup Settings 1 ");
        Assert.Single(request.SheetNumbers);
        Assert.Equal("saved", request.Mode);
        Assert.Equal(" PDF Export Setup Settings 1 ", request.SetupName);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData(" ")]
    [InlineData(",")]
    [InlineData("A,")]
    [InlineData(",A")]
    [InlineData("A,B,C")]
    [InlineData("A,A")]
    [InlineData("A, A")]
    public void RejectsInvalidSheetLists(string? numbers)
    {
        Assert.Throws<ArgumentException>(() => PdfDiagnosticRequest.Parse(numbers, "default", null));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("auto")]
    [InlineData("DEFAULT")]
    [InlineData(" saved ")]
    public void RejectsMissingOrInvalidMode(string? mode)
    {
        Assert.Throws<ArgumentException>(() => PdfDiagnosticRequest.Parse("A", mode, "setup"));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData(" ")]
    public void SavedModeRequiresSetupName(string? setup)
    {
        Assert.Throws<ArgumentException>(() => PdfDiagnosticRequest.Parse("A", "saved", setup));
    }

    [Theory]
    [InlineData("a")]
    [InlineData("A-1")]
    [InlineData("missing")]
    public void DoesNotUseCaseInsensitiveSubstringOrFallbackSelection(string number)
    {
        var request = PdfDiagnosticRequest.Parse(number, "default", null);
        Assert.Throws<InvalidOperationException>(() => request.SelectSheets(new[] { "A", "A-10" },
            sheet => sheet, _ => true));
    }

    [Fact]
    public void DuplicateSourceNumbersAreAmbiguousEvenIfOneIsPrintable()
    {
        var request = PdfDiagnosticRequest.Parse("A", "default", null);
        Assert.Throws<InvalidOperationException>(() => request.SelectSheets(new[] { ("A", true), ("A", false) },
            sheet => sheet.Item1, sheet => sheet.Item2));
    }

    [Fact]
    public void CannotPartiallyExportWhenSecondSheetIsMissing()
    {
        var request = PdfDiagnosticRequest.Parse("A,B", "default", null);
        Assert.Throws<InvalidOperationException>(() => request.SelectSheets(new[] { "A", "C" },
            sheet => sheet, _ => true));
    }

    [Theory]
    [InlineData(true, false)]
    [InlineData(false, false)]
    public void RejectsPlaceholderOrUnprintableSheet(bool placeholder, bool printable)
    {
        var request = PdfDiagnosticRequest.Parse("A", "default", null);
        Assert.Throws<InvalidOperationException>(() => request.SelectSheets(new[] { "A" },
            sheet => sheet, _ => !placeholder && printable));
    }
}
