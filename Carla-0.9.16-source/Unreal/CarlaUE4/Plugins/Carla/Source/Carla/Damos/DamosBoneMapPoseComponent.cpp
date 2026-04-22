#include "Carla/Damos/DamosBoneMapPoseComponent.h"

#include "ReferenceSkeleton.h"
#include "Components/PoseableMeshComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Logging/LogMacros.h"

DEFINE_LOG_CATEGORY_STATIC(LogDamosPoseSync, Log, All);

namespace
{
  static TArray<FTransform> BuildReferenceComponentSpaceTransforms(const FReferenceSkeleton& RefSkeleton)
  {
    const TArray<FTransform>& RefPose = RefSkeleton.GetRefBonePose();
    TArray<FTransform> Result;
    Result.SetNum(RefPose.Num());

    for (int32 BoneIndex = 0; BoneIndex < RefPose.Num(); ++BoneIndex)
    {
      const int32 ParentIndex = RefSkeleton.GetParentIndex(BoneIndex);
      Result[BoneIndex] =
          (ParentIndex != INDEX_NONE)
              ? RefPose[BoneIndex] * Result[ParentIndex]
              : RefPose[BoneIndex];
    }

    return Result;
  }
}

UDamosBoneMapPoseComponent::UDamosBoneMapPoseComponent()
{
  PrimaryComponentTick.bCanEverTick = true;
  PrimaryComponentTick.TickGroup = TG_PostPhysics;
}

void UDamosBoneMapPoseComponent::Initialize(
    USkeletalMeshComponent* InSourceMesh,
    UPoseableMeshComponent* InTargetMesh)
{
  SourceMesh = InSourceMesh;
  TargetMesh = InTargetMesh;
  RebuildBindings();
}

void UDamosBoneMapPoseComponent::TickComponent(
    float DeltaTime,
    enum ELevelTick TickType,
    FActorComponentTickFunction* ThisTickFunction)
{
  Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

  if ((SourceMesh == nullptr) || (TargetMesh == nullptr))
  {
    return;
  }

  SyncPose();
}

void UDamosBoneMapPoseComponent::RebuildBindings()
{
  BoneBindings.Reset();

  if ((SourceMesh == nullptr) || (TargetMesh == nullptr) ||
      (SourceMesh->SkeletalMesh == nullptr) || (TargetMesh->SkeletalMesh == nullptr))
  {
    UE_LOG(
        LogDamosPoseSync,
        Warning,
        TEXT("DAMOS pose sync skipped: source=%p target=%p source_mesh=%p target_mesh=%p"),
        SourceMesh,
        TargetMesh,
        (SourceMesh != nullptr) ? SourceMesh->SkeletalMesh : nullptr,
        (TargetMesh != nullptr) ? TargetMesh->SkeletalMesh : nullptr);
    return;
  }

  const FReferenceSkeleton& SourceRefSkeleton = SourceMesh->SkeletalMesh->RefSkeleton;
  const FReferenceSkeleton& TargetRefSkeleton = TargetMesh->SkeletalMesh->RefSkeleton;
  const TArray<FTransform>& SourceRefPose = SourceRefSkeleton.GetRefBonePose();
  const TArray<FTransform>& TargetRefPose = TargetRefSkeleton.GetRefBonePose();
  const TArray<FTransform> SourceReferenceComponent =
      BuildReferenceComponentSpaceTransforms(SourceRefSkeleton);
  const TArray<FTransform> TargetReferenceComponent =
      BuildReferenceComponentSpaceTransforms(TargetRefSkeleton);

  struct FBoneMappingRequest
  {
    TArray<FName> SourceCandidates;
    TArray<FName> TargetCandidates;
    bool bCopyTranslation = false;
  };

  const TArray<FBoneMappingRequest> MappingRequests = {
      {{TEXT("crl_hips__C")}, {TEXT("Hips"), TEXT("mixamorig:Hips")}, true},
      {{TEXT("crl_spine__C")}, {TEXT("Spine"), TEXT("mixamorig:Spine")}},
      {{TEXT("crl_spine01__C")}, {TEXT("Spine1"), TEXT("mixamorig:Spine1")}},
      {{TEXT("crl_neck__C")}, {TEXT("Spine2"), TEXT("mixamorig:Spine2")}},
      {{TEXT("crl_neck__C")}, {TEXT("Neck"), TEXT("mixamorig:Neck")}},
      {{TEXT("crl_Head__C")}, {TEXT("Head"), TEXT("mixamorig:Head")}},
      {{TEXT("ctrl_shoulder__L"), TEXT("crl_shoulder__L")}, {TEXT("LeftShoulder"), TEXT("mixamorig:LeftShoulder")}},
      {{TEXT("crl_arm__L")}, {TEXT("LeftArm"), TEXT("mixamorig:LeftArm")}},
      {{TEXT("crl_foreArm__L")}, {TEXT("LeftForeArm"), TEXT("mixamorig:LeftForeArm")}},
      {{TEXT("crl_hand__L")}, {TEXT("LeftHand"), TEXT("mixamorig:LeftHand")}},
      {{TEXT("crl_handThumb__L")}, {TEXT("LeftHandThumb1"), TEXT("mixamorig:LeftHandThumb1")}},
      {{TEXT("crl_handThumb01__L")}, {TEXT("LeftHandThumb2"), TEXT("mixamorig:LeftHandThumb2")}},
      {{TEXT("crl_handThumb02__L")}, {TEXT("LeftHandThumb3"), TEXT("mixamorig:LeftHandThumb3")}},
      {{TEXT("crl_handIndex__L")}, {TEXT("LeftHandIndex1"), TEXT("mixamorig:LeftHandIndex1")}},
      {{TEXT("crl_handIndex01__L")}, {TEXT("LeftHandIndex2"), TEXT("mixamorig:LeftHandIndex2")}},
      {{TEXT("crl_handIndex02__L")}, {TEXT("LeftHandIndex3"), TEXT("mixamorig:LeftHandIndex3")}},
      {{TEXT("crl_handMiddle_L"), TEXT("crl_handMiddle__L")}, {TEXT("LeftHandMiddle1"), TEXT("mixamorig:LeftHandMiddle1")}},
      {{TEXT("crl_handMiddle01__L")}, {TEXT("LeftHandMiddle2"), TEXT("mixamorig:LeftHandMiddle2")}},
      {{TEXT("crl_handMiddle02__L")}, {TEXT("LeftHandMiddle3"), TEXT("mixamorig:LeftHandMiddle3")}},
      {{TEXT("crl_handRing_L"), TEXT("crl_handRing__L")}, {TEXT("LeftHandRing1"), TEXT("mixamorig:LeftHandRing1")}},
      {{TEXT("crl_handRing01__L")}, {TEXT("LeftHandRing2"), TEXT("mixamorig:LeftHandRing2")}},
      {{TEXT("crl_handRing02__L")}, {TEXT("LeftHandRing3"), TEXT("mixamorig:LeftHandRing3")}},
      {{TEXT("crl_handPinky_L"), TEXT("crl_handPinky__L")}, {TEXT("LeftHandPinky1"), TEXT("mixamorig:LeftHandPinky1")}},
      {{TEXT("crl_handPinky01__L")}, {TEXT("LeftHandPinky2"), TEXT("mixamorig:LeftHandPinky2")}},
      {{TEXT("crl_handPinky02__L")}, {TEXT("LeftHandPinky3"), TEXT("mixamorig:LeftHandPinky3")}},
      {{TEXT("crl_shoulder__R")}, {TEXT("RightShoulder"), TEXT("mixamorig:RightShoulder")}},
      {{TEXT("crl_arm__R")}, {TEXT("RightArm"), TEXT("mixamorig:RightArm")}},
      {{TEXT("crl_foreArm__R")}, {TEXT("RightForeArm"), TEXT("mixamorig:RightForeArm")}},
      {{TEXT("crl_hand__R")}, {TEXT("RightHand"), TEXT("mixamorig:RightHand")}},
      {{TEXT("crl_handThumb__R")}, {TEXT("RightHandThumb1"), TEXT("mixamorig:RightHandThumb1")}},
      {{TEXT("crl_handThumb01__R")}, {TEXT("RightHandThumb2"), TEXT("mixamorig:RightHandThumb2")}},
      {{TEXT("crl_handThumb02__R")}, {TEXT("RightHandThumb3"), TEXT("mixamorig:RightHandThumb3")}},
      {{TEXT("crl_handIndex__R")}, {TEXT("RightHandIndex1"), TEXT("mixamorig:RightHandIndex1")}},
      {{TEXT("crl_handIndex01__R")}, {TEXT("RightHandIndex2"), TEXT("mixamorig:RightHandIndex2")}},
      {{TEXT("crl_handIndex02__R")}, {TEXT("RightHandIndex3"), TEXT("mixamorig:RightHandIndex3")}},
      {{TEXT("crl_handMiddle_R"), TEXT("crl_handMiddle__R")}, {TEXT("RightHandMiddle1"), TEXT("mixamorig:RightHandMiddle1")}},
      {{TEXT("crl_handMiddle01__R")}, {TEXT("RightHandMiddle2"), TEXT("mixamorig:RightHandMiddle2")}},
      {{TEXT("crl_handMiddle02__R")}, {TEXT("RightHandMiddle3"), TEXT("mixamorig:RightHandMiddle3")}},
      {{TEXT("crl_handRing_R"), TEXT("crl_handRing__R")}, {TEXT("RightHandRing1"), TEXT("mixamorig:RightHandRing1")}},
      {{TEXT("crl_handRing01__R")}, {TEXT("RightHandRing2"), TEXT("mixamorig:RightHandRing2")}},
      {{TEXT("crl_handRing02__R")}, {TEXT("RightHandRing3"), TEXT("mixamorig:RightHandRing3")}},
      {{TEXT("crl_handPinky_R"), TEXT("crl_handPinky__R")}, {TEXT("RightHandPinky1"), TEXT("mixamorig:RightHandPinky1")}},
      {{TEXT("crl_handPinky01__R")}, {TEXT("RightHandPinky2"), TEXT("mixamorig:RightHandPinky2")}},
      {{TEXT("crl_handPinky02__R")}, {TEXT("RightHandPinky3"), TEXT("mixamorig:RightHandPinky3")}},
      {{TEXT("crl_thigh__L")}, {TEXT("LeftUpLeg"), TEXT("mixamorig:LeftUpLeg")}},
      {{TEXT("crl_leg__L")}, {TEXT("LeftLeg"), TEXT("mixamorig:LeftLeg")}},
      {{TEXT("crl_foot__L")}, {TEXT("LeftFoot"), TEXT("mixamorig:LeftFoot")}},
      {{TEXT("crl_toe__L")}, {TEXT("LeftToeBase"), TEXT("mixamorig:LeftToeBase")}},
      {{TEXT("crl_thigh__R")}, {TEXT("RightUpLeg"), TEXT("mixamorig:RightUpLeg")}},
      {{TEXT("crl_leg__R")}, {TEXT("RightLeg"), TEXT("mixamorig:RightLeg")}},
      {{TEXT("crl_foot__R")}, {TEXT("RightFoot"), TEXT("mixamorig:RightFoot")}},
      {{TEXT("crl_toe__R")}, {TEXT("RightToeBase"), TEXT("mixamorig:RightToeBase")}},
  };

  auto FindFirstExistingSource =
      [&SourceRefSkeleton](const TArray<FName>& Candidates, FName& OutBone, int32& OutIndex) {
    for (const FName& Candidate : Candidates)
    {
      const int32 BoneIndex = SourceRefSkeleton.FindBoneIndex(Candidate);
      if (BoneIndex != INDEX_NONE)
      {
        OutBone = Candidate;
        OutIndex = BoneIndex;
        return true;
      }
    }
    return false;
  };

  auto FindFirstExistingTarget =
      [&TargetRefSkeleton](const TArray<FName>& Candidates, FName& OutBone, int32& OutIndex) {
    for (const FName& Candidate : Candidates)
    {
      const int32 BoneIndex = TargetRefSkeleton.FindBoneIndex(Candidate);
      if (BoneIndex != INDEX_NONE)
      {
        OutBone = Candidate;
        OutIndex = BoneIndex;
        return true;
      }
    }
    return false;
  };

  for (const FBoneMappingRequest& Request : MappingRequests)
  {
    FName SourceBone = NAME_None;
    int32 SourceBoneIndex = INDEX_NONE;
    if (!FindFirstExistingSource(Request.SourceCandidates, SourceBone, SourceBoneIndex))
    {
      continue;
    }
    FName TargetBone = NAME_None;
    int32 TargetBoneIndex = INDEX_NONE;
    if (!FindFirstExistingTarget(Request.TargetCandidates, TargetBone, TargetBoneIndex))
    {
      continue;
    }

    FBoneBinding Binding;
    Binding.SourceBone = SourceBone;
    Binding.TargetBone = TargetBone;
    Binding.SourceBoneIndex = SourceBoneIndex;
    Binding.TargetBoneIndex = TargetBoneIndex;
    Binding.SourceParentBoneIndex = SourceRefSkeleton.GetParentIndex(SourceBoneIndex);
    Binding.TargetParentBoneIndex = TargetRefSkeleton.GetParentIndex(TargetBoneIndex);
    Binding.SourceReferenceLocalTransform = SourceRefPose[SourceBoneIndex];
    Binding.SourceReferenceComponentTransform = SourceReferenceComponent[SourceBoneIndex];
    Binding.TargetReferenceLocalTransform = TargetRefPose[TargetBoneIndex];
    Binding.TargetReferenceComponentTransform = TargetReferenceComponent[TargetBoneIndex];
    Binding.TargetParentReferenceComponentTransform =
        (Binding.TargetParentBoneIndex != INDEX_NONE)
            ? TargetReferenceComponent[Binding.TargetParentBoneIndex]
            : FTransform::Identity;
    Binding.bCopyTranslation = Request.bCopyTranslation;
    BoneBindings.Add(Binding);
  }

  UE_LOG(
      LogDamosPoseSync,
      Display,
      TEXT("DAMOS pose sync built %d bindings from %s to %s"),
      BoneBindings.Num(),
      *SourceMesh->SkeletalMesh->GetName(),
      *TargetMesh->SkeletalMesh->GetName());
}

void UDamosBoneMapPoseComponent::SyncPose() const
{
  const TArray<FTransform>& SourceTransforms = SourceMesh->GetComponentSpaceTransforms();
  TMap<int32, FTransform> TargetComponentTransforms;
  TargetComponentTransforms.Reserve(BoneBindings.Num());

  for (const FBoneBinding& Binding : BoneBindings)
  {
    if (!SourceTransforms.IsValidIndex(Binding.SourceBoneIndex))
    {
      continue;
    }

    const FTransform& CurrentSourceComponentTransform = SourceTransforms[Binding.SourceBoneIndex];
    const FQuat DesiredTargetComponentRotation =
        (CurrentSourceComponentTransform.GetRotation() *
         Binding.SourceReferenceComponentTransform.GetRotation().Inverse() *
         Binding.TargetReferenceComponentTransform.GetRotation())
            .GetNormalized();

    FTransform TargetLocalTransform = Binding.TargetReferenceLocalTransform;
    FTransform ParentTargetComponentTransform = FTransform::Identity;
    if (Binding.TargetParentBoneIndex != INDEX_NONE)
    {
      if (const FTransform* ExistingParentTransform =
              TargetComponentTransforms.Find(Binding.TargetParentBoneIndex))
      {
        ParentTargetComponentTransform = *ExistingParentTransform;
      }
      else
      {
        ParentTargetComponentTransform = Binding.TargetParentReferenceComponentTransform;
      }
    }

    const FQuat TargetLocalRotation =
        (ParentTargetComponentTransform.GetRotation().Inverse() *
         DesiredTargetComponentRotation)
            .GetNormalized();
    TargetLocalTransform.SetRotation(TargetLocalRotation);
    if (Binding.bCopyTranslation)
    {
      FTransform CurrentSourceLocalTransform = CurrentSourceComponentTransform;
      if ((Binding.SourceParentBoneIndex != INDEX_NONE) &&
          SourceTransforms.IsValidIndex(Binding.SourceParentBoneIndex))
      {
        CurrentSourceLocalTransform =
            CurrentSourceComponentTransform.GetRelativeTransform(
                SourceTransforms[Binding.SourceParentBoneIndex]);
      }
      TargetLocalTransform.SetLocation(
          Binding.TargetReferenceLocalTransform.GetLocation() +
          (CurrentSourceLocalTransform.GetLocation() -
           Binding.SourceReferenceLocalTransform.GetLocation()));
    }

    FTransform TargetComponentTransform =
        (Binding.TargetParentBoneIndex != INDEX_NONE)
            ? (TargetLocalTransform * ParentTargetComponentTransform)
            : TargetLocalTransform;

    TargetComponentTransforms.Add(Binding.TargetBoneIndex, TargetComponentTransform);
    TargetMesh->SetBoneTransformByName(
        Binding.TargetBone,
        TargetComponentTransform,
        EBoneSpaces::ComponentSpace);
  }

  TargetMesh->RefreshBoneTransforms(nullptr);
}
