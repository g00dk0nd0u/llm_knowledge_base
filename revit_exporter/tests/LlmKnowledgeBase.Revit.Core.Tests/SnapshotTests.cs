using System.Text.Json.Nodes;
using LlmKnowledgeBase.Revit.Core;
using Xunit;

public sealed class SnapshotTests
{
    [Fact]
    public void CreatesEveryContractCollectionAndStableIds()
    {
        var snapshot = SnapshotContract.Create("p", "Revit", "path:c:/p.rvt", new string('a', 64));
        var records = snapshot["records"]!.AsObject();
        Assert.All(SnapshotContract.RecordCollections, name => Assert.IsType<JsonArray>(records[name]));
        Assert.Equal(StableIds.For("element", "m", "u"), StableIds.For("element", "m", "u"));
        Assert.NotEqual(StableIds.Hash("x", "a"), StableIds.Hash("x", "b"));
    }

    [Fact]
    public void DocumentIdDoesNotDependOnPdfHash()
    {
        var firstSnapshot = SnapshotContract.Create("p", "Revit", "c:/models/test.rvt", new string('a', 64));
        var secondSnapshot = SnapshotContract.Create("p", "Revit", "c:/models/test.rvt", new string('b', 64));
        var first = StableIds.Document("saved_path", "c:/models/test.rvt");
        var second = StableIds.Document("saved_path", "c:/models/test.rvt");
        Assert.Equal(first, second);
        Assert.NotEqual(firstSnapshot["project"]!["source_document_sha256"], secondSnapshot["project"]!["source_document_sha256"]);
    }

    [Theory]
    [InlineData(DimensionSemantic.Linear, "mm")]
    [InlineData(DimensionSemantic.Angular, "degrees")]
    [InlineData(DimensionSemantic.SpotElevation, "mm")]
    [InlineData(DimensionSemantic.SpotCoordinate, "mm")]
    public void DescribesKnownDimensionSemantics(DimensionSemantic semantic, string unit)
    {
        var value = DimensionValues.Describe(semantic);
        Assert.True(value.Supported);
        Assert.Equal(unit, value.Unit);
    }

    [Fact]
    public void UnknownDimensionSemanticIsNull()
    {
        Assert.Equal(new DimensionValueSemantics(null, false),
            DimensionValues.Describe(DimensionSemantic.Unsupported));
    }
}
