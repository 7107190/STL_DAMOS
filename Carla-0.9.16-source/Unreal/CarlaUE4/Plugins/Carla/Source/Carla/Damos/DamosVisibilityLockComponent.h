#pragma once

#include "Components/ActorComponent.h"

#include "DamosVisibilityLockComponent.generated.h"

class UPrimitiveComponent;

UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))
class CARLA_API UDamosVisibilityLockComponent : public UActorComponent
{
  GENERATED_BODY()

public:

  UDamosVisibilityLockComponent();

  void Initialize(const TArray<UPrimitiveComponent*>& InComponentsToKeep);

  void TickComponent(
      float DeltaTime,
      enum ELevelTick TickType,
      FActorComponentTickFunction* ThisTickFunction) override;

private:

  void HideForeignVisuals() const;
  bool ShouldKeepComponent(const UPrimitiveComponent* PrimitiveComponent) const;

  TArray<TWeakObjectPtr<UPrimitiveComponent>> ComponentsToKeep;
};
