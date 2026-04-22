#include "Carla/Damos/DamosVisibilityLockComponent.h"

#include "Components/PrimitiveComponent.h"
#include "GameFramework/Actor.h"

namespace
{
  static void HidePrimitiveVisualsOnActor(
      AActor* Actor,
      const TFunctionRef<bool(const UPrimitiveComponent*)>& ShouldKeepComponent)
  {
    if (Actor == nullptr)
    {
      return;
    }

    TArray<UPrimitiveComponent*> PrimitiveComponents;
    Actor->GetComponents<UPrimitiveComponent>(PrimitiveComponents);
    for (UPrimitiveComponent* PrimitiveComponent : PrimitiveComponents)
    {
      if ((PrimitiveComponent == nullptr) || ShouldKeepComponent(PrimitiveComponent))
      {
        continue;
      }

      PrimitiveComponent->SetVisibility(false, true);
      PrimitiveComponent->SetHiddenInGame(true, true);
      PrimitiveComponent->SetCastShadow(false);
    }
  }

  static void HidePrimitiveVisualsRecursive(
      AActor* Actor,
      const TFunctionRef<bool(const UPrimitiveComponent*)>& ShouldKeepComponent)
  {
    HidePrimitiveVisualsOnActor(Actor, ShouldKeepComponent);

    if (Actor == nullptr)
    {
      return;
    }

    TArray<AActor*> AttachedActors;
    Actor->GetAttachedActors(AttachedActors);
    for (AActor* AttachedActor : AttachedActors)
    {
      HidePrimitiveVisualsRecursive(AttachedActor, ShouldKeepComponent);
    }
  }
}

UDamosVisibilityLockComponent::UDamosVisibilityLockComponent()
{
  PrimaryComponentTick.bCanEverTick = true;
  PrimaryComponentTick.TickGroup = TG_PostPhysics;
}

void UDamosVisibilityLockComponent::Initialize(const TArray<UPrimitiveComponent*>& InComponentsToKeep)
{
  ComponentsToKeep.Reset();
  ComponentsToKeep.Reserve(InComponentsToKeep.Num());
  for (UPrimitiveComponent* PrimitiveComponent : InComponentsToKeep)
  {
    if (PrimitiveComponent != nullptr)
    {
      ComponentsToKeep.Add(PrimitiveComponent);
    }
  }

  HideForeignVisuals();
}

void UDamosVisibilityLockComponent::TickComponent(
    float DeltaTime,
    enum ELevelTick TickType,
    FActorComponentTickFunction* ThisTickFunction)
{
  Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
  HideForeignVisuals();
}

void UDamosVisibilityLockComponent::HideForeignVisuals() const
{
  HidePrimitiveVisualsRecursive(GetOwner(), [this](const UPrimitiveComponent* PrimitiveComponent) {
    return ShouldKeepComponent(PrimitiveComponent);
  });
}

bool UDamosVisibilityLockComponent::ShouldKeepComponent(
    const UPrimitiveComponent* PrimitiveComponent) const
{
  for (const TWeakObjectPtr<UPrimitiveComponent>& PreservedComponent : ComponentsToKeep)
  {
    if (PreservedComponent.Get() == PrimitiveComponent)
    {
      return true;
    }
  }
  return false;
}
