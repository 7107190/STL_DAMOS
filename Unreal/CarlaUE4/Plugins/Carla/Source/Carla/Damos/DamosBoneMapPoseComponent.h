#pragma once

#include "Components/ActorComponent.h"

#include "DamosBoneMapPoseComponent.generated.h"

class UPoseableMeshComponent;
class USkeletalMeshComponent;

UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))
class CARLA_API UDamosBoneMapPoseComponent : public UActorComponent
{
  GENERATED_BODY()

public:

  UDamosBoneMapPoseComponent();

  void Initialize(USkeletalMeshComponent* InSourceMesh, UPoseableMeshComponent* InTargetMesh);

  void TickComponent(
      float DeltaTime,
      enum ELevelTick TickType,
      FActorComponentTickFunction* ThisTickFunction) override;

private:

  struct FBoneBinding
  {
    FName SourceBone;
    FName TargetBone;
    int32 SourceBoneIndex = INDEX_NONE;
    int32 TargetBoneIndex = INDEX_NONE;
    int32 SourceParentBoneIndex = INDEX_NONE;
    int32 TargetParentBoneIndex = INDEX_NONE;
    FTransform SourceReferenceLocalTransform;
    FTransform SourceReferenceComponentTransform;
    FTransform TargetReferenceLocalTransform;
    FTransform TargetReferenceComponentTransform;
    FTransform TargetParentReferenceComponentTransform;
    bool bCopyTranslation = false;
  };

  void RebuildBindings();
  void SyncPose() const;

  UPROPERTY(Transient)
  USkeletalMeshComponent* SourceMesh = nullptr;

  UPROPERTY(Transient)
  UPoseableMeshComponent* TargetMesh = nullptr;

  TArray<FBoneBinding> BoneBindings;
};
