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
}
