using System.Collections.Generic;
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
        Assert.Equal(1, SnapshotContract.Version);
        Assert.IsType<JsonArray>(records["drawing_references"]);
    }

    [Fact]
    public void DrawingReferenceIdsAreStablePerMarkerOccurrenceAndNotTarget()
    {
        var evidence = StableIds.ReferenceEvidence("model", "marker", "sheet-a");
        var reference = StableIds.DrawingReference("model", "marker", "sheet-a", "callout_to");

        Assert.Equal(evidence, StableIds.ReferenceEvidence("model", "marker", "sheet-a"));
        Assert.Equal(reference, StableIds.DrawingReference("model", "marker", "sheet-a", "callout_to"));
        Assert.NotEqual(evidence, StableIds.ReferenceEvidence("model", "marker", "sheet-b"));
        Assert.NotEqual(reference, StableIds.DrawingReference("model", "marker", "sheet-b", "callout_to"));
        Assert.NotEqual(reference, StableIds.DrawingReference("model", "marker", "sheet-a", "elevation_reference_to"));
    }

    [Fact]
    public void OrdinaryCalloutIdsAreStableAndScopedToTheExplicitRelationshipOccurrence()
    {
        var evidence = StableIds.OrdinaryCalloutEvidence("model", "parent", "callout", "sheet");
        var reference = StableIds.OrdinaryCalloutDrawingReference(
            "model", "parent", "callout", "sheet", "callout_to");

        Assert.Equal(evidence,
            StableIds.OrdinaryCalloutEvidence("model", "parent", "callout", "sheet"));
        Assert.Equal(reference, StableIds.OrdinaryCalloutDrawingReference(
            "model", "parent", "callout", "sheet", "callout_to"));
        Assert.NotEqual(evidence,
            StableIds.OrdinaryCalloutEvidence("model", "other-parent", "callout", "sheet"));
        Assert.NotEqual(evidence,
            StableIds.OrdinaryCalloutEvidence("model", "parent", "other-callout", "sheet"));
        Assert.NotEqual(evidence,
            StableIds.OrdinaryCalloutEvidence("model", "parent", "callout", "other-sheet"));
        Assert.NotEqual(reference,
            StableIds.DrawingReference("model", "callout", "sheet", "callout_to"));
    }

    [Fact]
    public void OrdinaryElevationIdsAreStableAndScopedToTheMarkerSlotOccurrence()
    {
        var evidence = StableIds.OrdinaryElevationEvidence("model", "source", "marker", 2, "sheet");
        var reference = StableIds.OrdinaryElevationDrawingReference(
            "model", "source", "marker", 2, "sheet", "elevation_reference_to");

        Assert.Equal(evidence, StableIds.OrdinaryElevationEvidence("model", "source", "marker", 2, "sheet"));
        Assert.Equal(reference, StableIds.OrdinaryElevationDrawingReference(
            "model", "source", "marker", 2, "sheet", "elevation_reference_to"));
        Assert.NotEqual(evidence, StableIds.OrdinaryElevationEvidence("model", "other-source", "marker", 2, "sheet"));
        Assert.NotEqual(evidence, StableIds.OrdinaryElevationEvidence("model", "source", "other-marker", 2, "sheet"));
        Assert.NotEqual(evidence, StableIds.OrdinaryElevationEvidence("model", "source", "marker", 0, "sheet"));
        Assert.NotEqual(evidence, StableIds.OrdinaryElevationEvidence("model", "source", "marker", 2, "other-sheet"));
        Assert.NotEqual(reference, StableIds.DrawingReference("model", "marker", "sheet", "elevation_reference_to"));
        Assert.NotEqual(reference, StableIds.OrdinaryCalloutDrawingReference(
            "model", "source", "marker", "sheet", "elevation_reference_to"));
    }

    [Fact]
    public void ScheduleAppearanceIdsAreStableAndScopedToEverySourceOccurrence()
    {
        var appearance = StableIds.ScheduleAppearance(
            "element", "door", "schedule", "instance", "sheet");

        Assert.Equal(appearance, StableIds.ScheduleAppearance(
            "element", "door", "schedule", "instance", "sheet"));
        Assert.NotEqual(appearance, StableIds.ScheduleAppearance(
            "element_type", "door", "schedule", "instance", "sheet"));
        Assert.NotEqual(appearance, StableIds.ScheduleAppearance(
            "element", "other", "schedule", "instance", "sheet"));
        Assert.NotEqual(appearance, StableIds.ScheduleAppearance(
            "element", "door", "other", "instance", "sheet"));
        Assert.NotEqual(appearance, StableIds.ScheduleAppearance(
            "element", "door", "schedule", "other", "sheet"));
        Assert.NotEqual(appearance, StableIds.ScheduleAppearance(
            "element", "door", "schedule", "instance", "other"));
    }

    [Theory]
    [InlineData(true, false, false, false, false, false, "schedule_membership_filtered_by_sheet_unsupported")]
    [InlineData(false, true, false, false, false, false, "schedule_membership_split_unsupported")]
    [InlineData(false, false, true, false, false, false, "schedule_membership_key_schedule_unsupported")]
    [InlineData(false, false, false, true, false, false, "schedule_membership_material_takeoff_unsupported")]
    [InlineData(false, false, false, false, true, false, "schedule_membership_linked_files_unsupported")]
    [InlineData(false, false, false, false, false, true, "schedule_membership_embedded_schedule_unsupported")]
    public void RejectsScheduleModesWithoutSafePageMembership(bool filtered, bool split,
        bool key, bool material, bool linked, bool embedded, string warning)
    {
        var decision = ScheduleMembershipPolicy.Decide(
            filtered, split, key, material, linked, embedded);

        Assert.False(decision.Supported);
        Assert.Equal(warning, decision.WarningCode);
    }

    [Fact]
    public void SupportsOrdinaryUnsplitHostSchedule()
    {
        Assert.Equal(new ScheduleMembershipDecision(true, null),
            ScheduleMembershipPolicy.Decide(false, false, false, false, false, false));
    }

    [Fact]
    public void SpatialIdsAreScopedToTheirSourceModelAndRemainStable()
    {
        var host = StableIds.For("space", "model-host", "room-unique-id");
        var linked = StableIds.For("space", "model-link", "room-unique-id");

        Assert.Equal(host, StableIds.For("space", "model-host", "room-unique-id"));
        Assert.NotEqual(host, linked);
        Assert.Equal(2, new HashSet<string> { host, linked, linked }.Count);
    }

    [Fact]
    public void LinkedSpatialBoundaryIdsAreScopedToLinkOccurrence()
    {
        var first = StableIds.Hash("boundary", "space-id", "link-instance-a", "0");
        var second = StableIds.Hash("boundary", "space-id", "link-instance-b", "0");

        Assert.NotEqual(first, second);
        Assert.Equal(first, StableIds.Hash("boundary", "space-id", "link-instance-a", "0"));
        Assert.NotEqual(
            StableIds.Hash("boundary-segment", first, "0"),
            StableIds.Hash("boundary-segment", second, "0"));
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
