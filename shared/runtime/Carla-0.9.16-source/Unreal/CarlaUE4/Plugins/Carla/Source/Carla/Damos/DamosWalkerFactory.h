#pragma once

#include "Carla/Actor/CarlaActorFactory.h"

#include "DamosWalkerFactory.generated.h"

UCLASS()
class CARLA_API ADamosWalkerFactory final : public ACarlaActorFactory
{
  GENERATED_BODY()

public:

  TArray<FActorDefinition> GetDefinitions() override;

  FActorSpawnResult SpawnActor(
      const FTransform& SpawnAtTransform,
      const FActorDescription& ActorDescription) override;
};
