#include "Carla/Damos/DamosWalkerFactory.h"

#include "Carla/Actor/ActorBlueprintFunctionLibrary.h"
#include "Carla/Actor/ActorSpawnResult.h"
#include "Carla/Actor/PedestrianParameters.h"
#include "Carla/Damos/DamosBoneMapPoseComponent.h"
#include "Carla/Damos/DamosVisibilityLockComponent.h"
#include "Carla/Walker/WalkerController.h"
#include "Carla/Walker/WalkerBase.h"

#include "Components/PoseableMeshComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/SkinnedMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/MeshComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Character.h"
#include "Materials/MaterialInterface.h"

namespace
{
  namespace DamosWalkerIds
  {
    static const FString DeliveryBot = TEXT("walker.pedestrian.damos_deliverybot");
    static const FString Humanoid = TEXT("walker.pedestrian.damos_humanoid");
  }

  namespace DamosWalkerAssets
  {
    static constexpr TCHAR DeliveryBotBaseClass[] =
        TEXT("/Game/Carla/Blueprints/Walkers/BP_Walker_Male_EuroW_Owv.BP_Walker_Male_EuroW_Owv_C");
    static constexpr TCHAR HumanoidBaseClass[] =
        TEXT("/Game/Carla/Blueprints/Walkers/BP_Walker_Male1_v1.BP_Walker_Male1_v1_C");
    static constexpr TCHAR DeliveryBotMesh[] =
        TEXT("/Game/Damos/Walkers/DeliveryBot/DeliveryBot.DeliveryBot");
    static constexpr TCHAR HumanoidMesh[] =
        TEXT("/Game/Damos/Walkers/Humanoid/CHR_R_Maxim.CHR_R_Maxim_CHR_R_maxim");
    static constexpr TCHAR HumanoidMaterial[] =
        TEXT("/Game/Damos/Walkers/Humanoid/M_DamosHumanoidPBR.M_DamosHumanoidPBR");
  }

  template <typename TObjectType>
  static TObjectType* LoadAsset(const TCHAR* AssetPath)
  {
    return LoadObject<TObjectType>(nullptr, AssetPath);
  }

  static TSubclassOf<ACharacter> LoadWalkerClass(const TCHAR* ClassPath)
  {
    return LoadClass<ACharacter>(nullptr, ClassPath);
  }

  static void HideBaseWalkerVisuals(ACharacter* Character)
  {
    if (Character == nullptr)
    {
      return;
    }

    TArray<UMeshComponent*> MeshComponents;
    Character->GetComponents<UMeshComponent>(MeshComponents);
    for (UMeshComponent* MeshComponent : MeshComponents)
    {
      if (MeshComponent == nullptr)
      {
        continue;
      }

      if (USkeletalMeshComponent* SkeletalMeshComponent = Cast<USkeletalMeshComponent>(MeshComponent))
      {
        // The hidden source walker still needs to animate so the visible custom mesh
        // can copy a live pose instead of staying frozen in the reference pose.
        SkeletalMeshComponent->VisibilityBasedAnimTickOption =
            EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        SkeletalMeshComponent->bEnableUpdateRateOptimizations = false;
        SkeletalMeshComponent->bPauseAnims = false;
        SkeletalMeshComponent->bNoSkeletonUpdate = false;
        SkeletalMeshComponent->SetComponentTickEnabled(true);
        SkeletalMeshComponent->RefreshBoneTransforms(nullptr);
      }

      MeshComponent->SetVisibility(false, true);
      MeshComponent->SetHiddenInGame(true, true);
      MeshComponent->SetCastShadow(false);
    }
  }

  static USceneComponent* GetAttachParent(ACharacter* Character)
  {
    if ((Character != nullptr) && (Character->GetMesh() != nullptr))
    {
      return Character->GetMesh();
    }
    return (Character != nullptr) ? Character->GetRootComponent() : nullptr;
  }

  static float GetMeshHeightCm(const USkeletalMeshComponent* MeshComponent)
  {
    if ((MeshComponent == nullptr) || (MeshComponent->SkeletalMesh == nullptr))
    {
      return 180.0f;
    }
    return MeshComponent->SkeletalMesh->GetBounds().BoxExtent.Z * 2.0f;
  }

  static float GetStaticMeshHeightCm(const UStaticMesh* StaticMesh)
  {
    if (StaticMesh == nullptr)
    {
      return 100.0f;
    }
    return StaticMesh->GetBounds().BoxExtent.Z * 2.0f;
  }

  static float GetSkeletalMeshHeightCm(const USkeletalMesh* SkeletalMesh)
  {
    if (SkeletalMesh == nullptr)
    {
      return 180.0f;
    }
    return SkeletalMesh->GetBounds().BoxExtent.Z * 2.0f;
  }

  static UStaticMeshComponent* AttachDeliveryBotVisual(ACharacter* Character)
  {
    UStaticMesh* DeliveryBotMesh = LoadAsset<UStaticMesh>(DamosWalkerAssets::DeliveryBotMesh);
    if ((Character == nullptr) || (DeliveryBotMesh == nullptr))
    {
      return nullptr;
    }

    UStaticMeshComponent* Visual = NewObject<UStaticMeshComponent>(Character, TEXT("DamosDeliveryBotVisual"));
    Visual->SetStaticMesh(DeliveryBotMesh);
    Visual->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Visual->SetGenerateOverlapEvents(false);
    Visual->SetCanEverAffectNavigation(false);
    Visual->SetMobility(EComponentMobility::Movable);

    USceneComponent* AttachParent = (Character != nullptr) ? Character->GetRootComponent() : nullptr;
    Visual->SetupAttachment(AttachParent);
    Visual->RegisterComponent();

    const float BaseHeight = GetMeshHeightCm(Character->GetMesh());
    const float VisualHeight = GetStaticMeshHeightCm(DeliveryBotMesh);
    const float Scale = (VisualHeight > 1.0f) ? ((BaseHeight * 0.82f) / VisualHeight) : 1.0f;
    const FVector BaseRelativeScale =
        (Character->GetMesh() != nullptr) ? Character->GetMesh()->GetRelativeScale3D() : FVector(1.0f);
    const FVector BaseRelativeLocation =
        (Character->GetMesh() != nullptr) ? Character->GetMesh()->GetRelativeLocation() : FVector::ZeroVector;
    const FRotator BaseRelativeRotation =
        (Character->GetMesh() != nullptr) ? Character->GetMesh()->GetRelativeRotation() : FRotator::ZeroRotator;

    Visual->SetRelativeScale3D(BaseRelativeScale * Scale);
    Visual->SetRelativeLocation(BaseRelativeLocation + FVector(0.0f, 0.0f, 16.0f));
    Visual->SetRelativeRotation(BaseRelativeRotation);
    return Visual;
  }

  static UPoseableMeshComponent* AttachHumanoidVisual(ACharacter* Character)
  {
    USkeletalMesh* HumanoidMesh = LoadAsset<USkeletalMesh>(DamosWalkerAssets::HumanoidMesh);
    if ((Character == nullptr) || (Character->GetMesh() == nullptr) || (HumanoidMesh == nullptr))
    {
      return nullptr;
    }

    UPoseableMeshComponent* Visual = NewObject<UPoseableMeshComponent>(Character, TEXT("DamosHumanoidVisual"));
    Visual->SetSkeletalMesh(HumanoidMesh);
    Visual->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Visual->SetGenerateOverlapEvents(false);
    Visual->SetCanEverAffectNavigation(false);
    Visual->SetMobility(EComponentMobility::Movable);
    Visual->SetVisibility(true, true);
    Visual->SetHiddenInGame(false, true);
    Visual->bComponentUseFixedSkelBounds = true;
    Visual->SetBoundsScale(4.0f);

    USceneComponent* AttachParent = Character->GetRootComponent();
    Visual->SetupAttachment(AttachParent);
    Visual->RegisterComponent();

    const float BaseHeight = GetMeshHeightCm(Character->GetMesh());
    const float VisualHeight = GetSkeletalMeshHeightCm(HumanoidMesh);
    const float Scale = (VisualHeight > 1.0f) ? (BaseHeight / VisualHeight) : 1.0f;

    const FVector BaseRelativeScale = Character->GetMesh()->GetRelativeScale3D();
    Visual->SetRelativeScale3D(BaseRelativeScale * Scale);
    Visual->SetRelativeLocation(Character->GetMesh()->GetRelativeLocation());
    Visual->SetRelativeRotation(Character->GetMesh()->GetRelativeRotation());

    if (UMaterialInterface* HumanoidMaterial =
            LoadAsset<UMaterialInterface>(DamosWalkerAssets::HumanoidMaterial))
    {
      const int32 MaterialSlots = FMath::Max(1, Visual->GetNumMaterials());
      for (int32 MaterialIndex = 0; MaterialIndex < MaterialSlots; ++MaterialIndex)
      {
        Visual->SetMaterial(MaterialIndex, HumanoidMaterial);
      }
    }

    UDamosBoneMapPoseComponent* PoseSync =
        NewObject<UDamosBoneMapPoseComponent>(Character, TEXT("DamosHumanoidPoseSync"));
    PoseSync->RegisterComponent();
    PoseSync->AddTickPrerequisiteComponent(Character->GetMesh());
    PoseSync->Initialize(Character->GetMesh(), Visual);
    return Visual;
  }

  static void PreferWheelchairByDefault(FActorDefinition& Definition)
  {
    for (FActorVariation& Variation : Definition.Variations)
    {
      if (Variation.Id == TEXT("use_wheelchair"))
      {
        Variation.RecommendedValues = {TEXT("true"), TEXT("false")};
      }
    }
  }
}

TArray<FActorDefinition> ADamosWalkerFactory::GetDefinitions()
{
  using ABFL = UActorBlueprintFunctionLibrary;

  TArray<FActorDefinition> Definitions;

  const TSubclassOf<ACharacter> DeliveryBotBaseClass =
      LoadWalkerClass(DamosWalkerAssets::DeliveryBotBaseClass);
  const TSubclassOf<ACharacter> HumanoidBaseClass =
      LoadWalkerClass(DamosWalkerAssets::HumanoidBaseClass);

  if (DeliveryBotBaseClass != nullptr)
  {
    FPedestrianParameters DeliveryBotParameters;
    DeliveryBotParameters.Id = TEXT("damos_deliverybot");
    DeliveryBotParameters.Class = DeliveryBotBaseClass;
    DeliveryBotParameters.Gender = EPedestrianGender::Other;
    DeliveryBotParameters.Age = EPedestrianAge::Adult;
    DeliveryBotParameters.Generation = 4;
    DeliveryBotParameters.Speed = {0.8f, 1.2f};
    DeliveryBotParameters.bCanUseWheelChair = true;

    bool bSuccess = false;
    FActorDefinition Definition;
    ABFL::MakePedestrianDefinition(DeliveryBotParameters, bSuccess, Definition);
    if (bSuccess)
    {
      PreferWheelchairByDefault(Definition);
      Definitions.Add(Definition);
    }
  }

  if (HumanoidBaseClass != nullptr)
  {
    FPedestrianParameters HumanoidParameters;
    HumanoidParameters.Id = TEXT("damos_humanoid");
    HumanoidParameters.Class = HumanoidBaseClass;
    HumanoidParameters.Gender = EPedestrianGender::Other;
    HumanoidParameters.Age = EPedestrianAge::Adult;
    HumanoidParameters.Generation = 4;
    HumanoidParameters.Speed = {1.0f, 1.4f};
    HumanoidParameters.bCanUseWheelChair = false;

    bool bSuccess = false;
    FActorDefinition Definition;
    ABFL::MakePedestrianDefinition(HumanoidParameters, bSuccess, Definition);
    if (bSuccess)
    {
      Definitions.Add(Definition);
    }
  }

  return Definitions;
}

FActorSpawnResult ADamosWalkerFactory::SpawnActor(
    const FTransform& SpawnAtTransform,
    const FActorDescription& ActorDescription)
{
  using ABFL = UActorBlueprintFunctionLibrary;

  UWorld* World = GetWorld();
  if ((World == nullptr) || (ActorDescription.Class == nullptr))
  {
    return {};
  }

  FActorSpawnParameters SpawnParameters;
  SpawnParameters.SpawnCollisionHandlingOverride =
      ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;

  ACharacter* Character =
      World->SpawnActor<ACharacter>(ActorDescription.Class, SpawnAtTransform, SpawnParameters);
  if (Character == nullptr)
  {
    return {};
  }

  Character->AIControllerClass = AWalkerController::StaticClass();
  Character->AutoPossessAI = EAutoPossessAI::PlacedInWorldOrSpawned;
  if (Character->GetController() == nullptr)
  {
    Character->SpawnDefaultController();
  }

  if (AWalkerBase* WalkerBase = Cast<AWalkerBase>(Character))
  {
    const bool bUseWheelChair =
        ABFL::RetrieveActorAttributeToBool(
            TEXT("use_wheelchair"),
            ActorDescription.Variations,
            ActorDescription.Id == DamosWalkerIds::DeliveryBot);
    WalkerBase->bUsesWheelChair = bUseWheelChair;
  }

  HideBaseWalkerVisuals(Character);

  bool bAttached = false;
  if (ActorDescription.Id == DamosWalkerIds::DeliveryBot)
  {
    UStaticMeshComponent* DeliveryBotVisual = AttachDeliveryBotVisual(Character);
    bAttached = (DeliveryBotVisual != nullptr);
    if (bAttached)
    {
      UDamosVisibilityLockComponent* VisibilityLock =
          NewObject<UDamosVisibilityLockComponent>(Character, TEXT("DamosDeliveryBotVisibilityLock"));
      VisibilityLock->RegisterComponent();
      VisibilityLock->Initialize({Character->GetCapsuleComponent(), DeliveryBotVisual});
    }
  }
  else
  {
    UPoseableMeshComponent* HumanoidVisual = AttachHumanoidVisual(Character);
    bAttached = (HumanoidVisual != nullptr);
  }

  if (!bAttached)
  {
    Character->Destroy();
    return {};
  }

  return FActorSpawnResult(Character);
}
